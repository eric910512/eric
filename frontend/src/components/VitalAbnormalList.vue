<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import NoteComposer from '@/components/NoteComposer.vue'
import { useNurseStore } from '@/stores/nurse'
import { formatWhen } from '@/utils/format'
import { BP_SITES, VITAL_FIELDS } from '@/utils/vitals'

/**
 * Nurse: readings outside the reference ranges across the caseload (GET /vital-signs/abnormal).
 * Alerts raised by a reading can be resolved here — same notification workflow as symptoms.
 */
const props = defineProps({
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})
const emit = defineEmits(['select-patient'])

const store = useNurseStore()
const hours = ref(72)
const openKey = ref(null)
const notes = reactive({})
const errors = reactive({})
const busy = ref(null)

const data = computed(() => store.abnormalVitals)
const LEVEL = {
  critical: { chip: 'bg-critical text-white', dot: 'bg-critical', text: 'text-critical' },
  warning: { chip: 'bg-warn-soft text-warn', dot: 'bg-warn', text: 'text-warn' },
}
const SHOWN = ['temperature_c', 'heart_rate_bpm', 'blood_pressure', 'spo2_pct', 'respiratory_rate']

function readings(item) {
  const flagged = Object.fromEntries(item.flags.map((f) => [f.field, f.level]))
  return SHOWN.flatMap((key) => {
    if (key === 'blood_pressure') {
      if (item.systolic_bp_mmhg == null) return []
      const level = flagged.systolic_bp_mmhg ?? flagged.diastolic_bp_mmhg ?? null
      const site = item.bp_measure_site ? `（${BP_SITES[item.bp_measure_site]}）` : ''
      return [{ key, text: `血壓 ${item.systolic_bp_mmhg}/${item.diastolic_bp_mmhg}${site}`, level }]
    }
    if (item[key] == null) return []
    const meta = VITAL_FIELDS[key]
    return [{ key, text: `${meta.label} ${item[key]}${meta.unit}`, level: flagged[key] ?? null }]
  })
}

function load() {
  store.fetchAbnormalVitals(hours.value)
}
onMounted(load)

async function resolve(alert) {
  const note = (notes[alert.event_key] ?? '').trim()
  if (!note) {
    errors[alert.event_key] = '請填寫處理說明。'
    return
  }
  busy.value = alert.event_key
  errors[alert.event_key] = null
  try {
    await store.resolve(alert.my_notification_id, note) // refreshes this list, the notification list and dashboards
    openKey.value = null
  } catch (err) {
    errors[alert.event_key] = err.message
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface" aria-label="生命徵象異常">
    <header class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
      <AppIcon name="thermometer" class="text-care" />
      <h2 class="text-lg font-bold">生命徵象異常</h2>
      <span v-if="data?.meta.critical" class="rounded-full bg-critical px-2 text-sm font-bold text-white">危急 {{ data.meta.critical }}</span>
      <span v-if="data?.meta.warning" class="rounded-full bg-warn-soft px-2 text-sm font-bold text-warn">注意 {{ data.meta.warning }}</span>
      <label class="ml-auto flex items-center gap-2 text-sm text-ink-soft">
        範圍
        <select v-model.number="hours" class="min-h-9 rounded-lg border border-line bg-surface px-2 text-ink" @change="load">
          <option :value="24">24 小時</option>
          <option :value="72">3 天</option>
          <option :value="168">7 天</option>
        </select>
      </label>
    </header>

    <p v-if="store.errors.abnormal" class="px-5 py-4 text-critical">{{ store.errors.abnormal }}</p>
    <div v-else-if="!data" class="space-y-3 p-5" aria-busy="true">
      <div v-for="n in 2" :key="n" class="h-16 animate-pulse rounded bg-mist" />
    </div>
    <p v-else-if="!data.items.length" class="px-5 py-4 text-ink-soft">這段期間沒有超出範圍的生命徵象。</p>

    <ul v-else class="divide-y divide-line">
      <li v-for="item in data.items" :key="item.id" class="px-5 py-4">
        <div class="flex gap-3">
          <i class="mt-2 size-2.5 shrink-0 rounded-full" :class="LEVEL[item.severity].dot" />
          <div class="min-w-0 flex-1">
            <p class="flex flex-wrap items-baseline gap-x-3">
              <button type="button" class="font-bold hover:underline" @click="emit('select-patient', item.patient.id)">
                {{ item.patient.display_name }}（{{ item.patient.patient_code }}）
              </button>
              <span v-if="item.cycle_day" class="text-sm text-ink-soft">療程第 {{ item.cycle_day }} 天</span>
              <span v-if="item.in_nadir" class="rounded-full bg-nadir-soft px-2 text-sm font-bold text-nadir">骨髓抑制期</span>
              <span class="ml-auto text-sm text-ink-soft">{{ formatWhen(item.measured_at, props.today, props.timezone) }}{{ item.source === 'nurse' ? '，護理師量測' : '' }}</span>
            </p>

            <ul class="mt-2 flex flex-wrap gap-2">
              <li v-for="r in readings(item)" :key="r.key" class="rounded-lg px-3 py-1"
                  :class="r.level ? `${LEVEL[r.level].chip} font-bold` : 'bg-mist text-ink-soft'">{{ r.text }}</li>
            </ul>
            <p class="mt-1 text-sm" :class="LEVEL[item.severity].text">{{ item.flags[0].message }}</p>

            <ul v-if="item.alerts.length" class="mt-2 space-y-2">
              <li v-for="a in item.alerts" :key="a.event_key">
                <div v-if="a.resolved" class="rounded-lg bg-ok-soft px-3 py-2">
                  <p class="flex items-center gap-1.5 text-sm font-bold text-ok">
                    <AppIcon name="check" :size="16" />{{ a.title }}：已由 {{ a.resolved_by }} 處理
                  </p>
                  <p v-if="a.resolution_note" class="mt-0.5 text-sm">{{ a.resolution_note }}</p>
                </div>
                <div v-else class="rounded-lg border border-critical/40 px-3 py-2">
                  <div class="flex flex-wrap items-center gap-2">
                    <AppIcon name="alert" :size="18" :class="a.severity === 'critical' ? 'text-critical' : 'text-warn'" />
                    <span class="font-bold">{{ a.title }}</span>
                    <span class="text-sm text-ink-soft">未處理</span>
                    <button
                      v-if="a.my_notification_id && openKey !== a.event_key"
                      type="button"
                      class="ml-auto min-h-10 rounded-full bg-critical px-4 font-bold text-white hover:bg-critical/90"
                      @click="openKey = a.event_key"
                    >處理</button>
                  </div>
                  <form v-if="openKey === a.event_key" class="mt-3 space-y-3" @submit.prevent="resolve(a)">
                    <NoteComposer
                      v-model="notes[a.event_key]"
                      label="處理說明"
                      placeholder="例如：已電話聯繫病人，建議立即至急診"
                      :invalid="!!errors[a.event_key]"
                      @update:model-value="errors[a.event_key] = null"
                    />
                    <div class="flex flex-wrap gap-2">
                      <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90 disabled:opacity-60"
                              :disabled="busy === a.event_key">{{ busy === a.event_key ? '儲存中…' : '標示為已處理' }}</button>
                      <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" @click="openKey = null">取消</button>
                    </div>
                  </form>
                  <p v-if="errors[a.event_key]" class="mt-2 text-sm font-medium text-critical" role="alert">{{ errors[a.event_key] }}</p>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
