<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import NoteComposer from '@/components/NoteComposer.vue'
import { useNurseStore } from '@/stores/nurse'
import { formatWhen } from '@/utils/format'

/**
 * Nurse notification list on the dashboard: open risk alerts (with lifecycle status) and
 * resolved history. Quick 「處理」 closes an alert in one step; the full 接手 → 處理中 → 完成
 * flow is in the Notification Center. With `patientId` it shows only that patient's alerts.
 */
const props = defineProps({
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
  patientId: { type: String, default: null },
  title: { type: String, default: '未處理通知' },
  limit: { type: Number, default: 0 }, // 0 = show all
})
const emit = defineEmits(['select-patient'])

const store = useNurseStore()
const tab = ref('unresolved')
const expanded = ref(false)
const openId = ref(null)
const notes = reactive({})
const busy = ref(null)
const errors = reactive({})

const key = computed(() => `${tab.value}:${props.patientId ?? 'all'}`)
const list = computed(() => store.notifications[key.value])
const counts = computed(() => store.notifications[`unresolved:${props.patientId ?? 'all'}`]?.meta.total ?? 0)
const items = computed(() => {
  const all = list.value?.items ?? []
  return props.limit && !expanded.value ? all.slice(0, props.limit) : all
})

const SEVERITY = {
  critical: { dot: 'bg-critical', text: 'text-critical', label: '危急' },
  warning: { dot: 'bg-warn', text: 'text-warn', label: '注意' },
  info: { dot: 'bg-care', text: 'text-care', label: '資訊' },
}

function load() {
  store.fetchNotifications({ status: tab.value, patientId: props.patientId })
}
onMounted(() => {
  load()
  if (tab.value !== 'unresolved') store.fetchNotifications({ status: 'unresolved', patientId: props.patientId })
})
watch([tab, () => props.patientId], load)

async function markRead(n) {
  busy.value = n.id
  try {
    await store.markRead(n.id)
  } catch (err) {
    errors[n.id] = err.message
  } finally {
    busy.value = null
  }
}

async function resolve(n) {
  const note = (notes[n.id] ?? '').trim()
  if (!note) {
    errors[n.id] = '請填寫處理說明。'
    return
  }
  busy.value = n.id
  errors[n.id] = null
  try {
    await store.resolve(n.id, note)
    openId.value = null
    delete notes[n.id]
  } catch (err) {
    errors[n.id] = err.message
    if (err.code === 'INVALID_STATE') load() // someone else handled it: refresh to show who
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface" :aria-label="title">
    <header class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
      <AppIcon name="bell" class="text-care" />
      <h2 class="text-lg font-bold">{{ title }}</h2>
      <span v-if="counts" class="rounded-full bg-critical px-2 text-sm font-bold text-white">{{ counts }}</span>
      <div class="ml-auto flex rounded-full border border-line p-0.5" role="tablist">
        <button
          v-for="t in [{ k: 'unresolved', l: '未處理' }, { k: 'resolved', l: '已處理' }]"
          :key="t.k"
          type="button"
          role="tab"
          :aria-selected="tab === t.k"
          class="min-h-9 rounded-full px-4 text-sm"
          :class="tab === t.k ? 'bg-care font-bold text-white' : 'text-ink-soft hover:text-ink'"
          @click="tab = t.k"
        >{{ t.l }}</button>
      </div>
    </header>

    <p v-if="store.errors[key]" class="px-5 py-4 text-critical">{{ store.errors[key] }}</p>
    <div v-else-if="!list" class="space-y-3 p-5" aria-busy="true">
      <div v-for="n in 2" :key="n" class="h-14 animate-pulse rounded bg-mist" />
    </div>
    <p v-else-if="!items.length" class="px-5 py-4 text-ink-soft">
      {{ tab === 'unresolved' ? '目前沒有需要處理的警示。' : '還沒有已處理的紀錄。' }}
    </p>

    <ul v-else class="divide-y divide-line">
      <li v-for="n in items" :key="n.id" class="px-5 py-4">
        <div class="flex gap-3">
          <i class="mt-2 size-2.5 shrink-0 rounded-full" :class="(SEVERITY[n.severity] ?? SEVERITY.info).dot" />
          <div class="min-w-0 flex-1">
            <p class="flex flex-wrap items-baseline gap-x-2">
              <span class="font-bold" :class="(SEVERITY[n.severity] ?? SEVERITY.info).text">{{ n.title }}</span>
              <span v-if="tab === 'unresolved' && n.status !== 'new'" class="rounded-full px-2 text-xs font-bold" :class="n.status === 'in_progress' ? 'bg-warn-soft text-warn' : 'bg-care-soft text-care'">
                {{ n.status_text }}<template v-if="(n.status === 'in_progress' ? n.handling?.started : n.handling?.acknowledged)?.by">・{{ (n.status === 'in_progress' ? n.handling.started : n.handling.acknowledged).by.display_name }}</template>
              </span>
              <span v-if="n.is_mine && !n.is_read && tab === 'unresolved'" class="text-sm font-bold text-critical">未讀</span>
              <span class="ml-auto text-sm text-ink-soft">{{ formatWhen(n.created_at, today, timezone) }}</span>
            </p>
            <p class="mt-0.5">{{ n.message }}</p>
            <button
              v-if="n.patient && !patientId"
              type="button"
              class="mt-1 text-sm text-care underline-offset-2 hover:underline"
              @click="emit('select-patient', n.patient.id)"
            >查看 {{ n.patient.display_name }}（{{ n.patient.patient_code }}）</button>

            <!-- resolved state -->
            <div v-if="n.acknowledged" class="mt-2 rounded-lg bg-ok-soft px-3 py-2">
              <p class="flex items-center gap-1.5 text-sm font-bold text-ok">
                <AppIcon name="check" :size="16" />
                已處理：{{ n.acknowledged.by?.display_name }}，{{ formatWhen(n.acknowledged.at, today, timezone) }}
              </p>
              <p class="mt-0.5">{{ n.acknowledged.resolution_note }}</p>
            </div>

            <!-- actions -->
            <div v-else class="mt-2">
              <div v-if="openId !== n.id" class="flex flex-wrap gap-2">
                <button
                  v-if="n.is_mine"
                  type="button"
                  class="min-h-11 rounded-full bg-critical px-5 font-bold text-white hover:bg-critical/90"
                  @click="openId = n.id"
                >處理</button>
                <RouterLink
                  :to="{ name: 'nurse-notifications', query: { tab: n.status === 'in_progress' ? 'in_progress' : 'pending', id: n.id } }"
                  class="inline-flex min-h-11 items-center rounded-full border border-line px-4 font-medium text-care hover:bg-care-soft"
                >在通知中心開啟</RouterLink>
                <button
                  v-if="n.is_mine && !n.is_read"
                  type="button"
                  class="min-h-11 rounded-full border border-line px-4 hover:bg-mist disabled:opacity-60"
                  :disabled="busy === n.id"
                  @click="markRead(n)"
                >標示已讀</button>
              </div>
              <form v-else class="space-y-3" @submit.prevent="resolve(n)">
                <NoteComposer
                  v-model="notes[n.id]"
                  @update:model-value="errors[n.id] = null"
                  label="處理說明（內部紀錄；病人只會看到「已處理完成」）"
                  placeholder="例如：已電話聯繫病人，建議立即至急診"
                  :invalid="!!errors[n.id]"
                />
                <div class="flex flex-wrap gap-2">
                  <button
                    type="submit"
                    class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90 disabled:opacity-60"
                    :disabled="busy === n.id"
                  >{{ busy === n.id ? '儲存中…' : '標示為已處理' }}</button>
                  <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" @click="openId = null">取消</button>
                </div>
              </form>
              <p v-if="errors[n.id]" class="mt-2 text-sm font-medium text-critical" role="alert">{{ errors[n.id] }}</p>
            </div>
          </div>
        </div>
      </li>
    </ul>

    <button
      v-if="limit && (list?.items.length ?? 0) > limit"
      type="button"
      class="w-full border-t border-line py-3 font-medium text-care hover:bg-mist"
      @click="expanded = !expanded"
    >{{ expanded ? '收合' : `顯示全部 ${list.items.length} 則` }}</button>
  </section>
</template>
