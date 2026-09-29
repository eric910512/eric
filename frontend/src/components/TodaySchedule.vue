<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatDate, formatTime } from '@/utils/format'

/** today-schedule widget: appointments, preparation steps (黃底紅字), leave hotline. */
const props = defineProps({
  schedule: { type: Object, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const TYPE_ICON = {
  chemo_infusion: 'drop',
  lab_draw: 'drop',
  clinic_visit: 'clipboard',
  imaging: 'chart',
  education_session: 'users',
}
const STATUS = { checked_in: '已報到', completed: '已完成', no_show: '未到' }
const INSTRUCTION_ICON = { fasting: 'clock', check_in: 'clipboard', medication: 'pill', bring_item: 'check' }

// LiveClock: server_time plus elapsed time, so a wrong device clock does not matter.
const loadedAt = Date.now()
const now = ref(Date.now())
let timer
onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 15000)
})
onBeforeUnmount(() => clearInterval(timer))

const clock = computed(() => {
  const serverNow = new Date(props.schedule.server_time).getTime() + (now.value - loadedAt)
  return formatTime(new Date(serverNow).toISOString(), props.timezone)
})
</script>

<template>
  <WidgetFrame
    title="今日行程"
    icon="calendar"
    :state="schedule.appointments.length ? 'ready' : 'empty'"
    empty-text="今天沒有排定的行程。"
  >
    <template #meta>
      <span>{{ formatDate(schedule.date) }}</span>
      <time class="ml-2 font-bold text-ink" aria-label="現在時間">{{ clock }}</time>
    </template>

    <ul class="space-y-3">
      <li v-for="appt in schedule.appointments" :key="appt.id" class="flex items-center gap-4">
        <span class="grid size-12 shrink-0 place-items-center rounded-xl bg-care-soft text-care">
          <AppIcon :name="TYPE_ICON[appt.appointment_type] ?? 'calendar'" />
        </span>
        <div class="min-w-0">
          <p class="text-xl font-bold">
            <time>{{ formatTime(appt.scheduled_at, timezone) }}</time>
            <span class="ml-2">{{ appt.title }}</span>
          </p>
          <p v-if="appt.location || STATUS[appt.status]" class="text-ink-soft">
            {{ appt.location }}<span v-if="STATUS[appt.status]" class="ml-2 rounded-full bg-ok-soft px-2 py-0.5 text-sm font-bold text-ok" data-appointment-status>{{ STATUS[appt.status] }}</span>
          </p>
        </div>
      </li>
    </ul>

    <div v-if="schedule.highlight_instructions.length" class="mt-4 rounded-xl bg-action px-4 py-3">
      <p class="sr-only">出發前請注意</p>
      <ul class="flex flex-wrap gap-x-6 gap-y-2">
        <li
          v-for="item in schedule.highlight_instructions"
          :key="`${item.appointment_id}-${item.instruction_type}`"
          class="inline-flex items-center gap-2 text-xl font-bold text-action-ink"
        >
          <AppIcon :name="INSTRUCTION_ICON[item.instruction_type] ?? 'alert'" :size="24" />
          {{ item.text }}
        </li>
      </ul>
    </div>

    <a
      v-if="schedule.quick_contact"
      :href="`tel:${schedule.quick_contact.phone}`"
      class="mt-4 inline-flex min-h-12 items-center gap-2 rounded-full border border-line px-4 font-medium text-care hover:bg-care-soft"
    >
      <AppIcon name="phone" :size="20" />
      {{ schedule.quick_contact.label }} {{ schedule.quick_contact.phone }}
    </a>
  </WidgetFrame>
</template>
