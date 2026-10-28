<script setup>
import { computed } from 'vue'

import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatDate, formatShortDate, formatTime, localDate } from '@/utils/format'

/**
 * radiation-progress widget (patient home): 放射線治療進度, derived by the backend from the
 * patient's radiotherapy appointments (completed / scheduled sessions, today's and the next one).
 */
const props = defineProps({
  progress: { type: Object, default: null },
  timezone: { type: String, default: 'Asia/Taipei' },
  disclaimer: { type: String, default: '' },
  detailsTo: { type: Object, default: null },
})

const STATUS = { scheduled: '已排定', checked_in: '已報到', completed: '已完成', no_show: '未到' }

const pct = computed(() => {
  const p = props.progress
  return p?.total_sessions ? Math.round((p.completed_sessions / p.total_sessions) * 100) : 0
})
const remaining = computed(() => (props.progress ? props.progress.total_sessions - props.progress.completed_sessions : 0))
</script>

<template>
  <WidgetFrame
    title="放射線治療進度"
    icon="calendar"
    :state="progress ? 'ready' : 'empty'"
    empty-text="目前還沒有排定的放射線治療。"
  >
    <template v-if="progress" #meta>
      <span>{{ formatShortDate(progress.first_date) }}–{{ formatShortDate(progress.last_date) }}</span>
    </template>

    <template v-if="progress">
      <p class="flex flex-wrap items-baseline gap-x-2" data-radiation-count>
        <span class="text-ink-soft">已完成</span>
        <span class="text-4xl font-bold text-care">{{ progress.completed_sessions }}</span>
        <span class="text-xl font-bold">/ 共 {{ progress.total_sessions }} 次</span>
      </p>
      <div
        class="mt-2 h-3 overflow-hidden rounded-full bg-mist"
        role="progressbar"
        :aria-valuenow="progress.completed_sessions"
        aria-valuemin="0"
        :aria-valuemax="progress.total_sessions"
        :aria-label="`放射線治療已完成 ${progress.completed_sessions} 次，共 ${progress.total_sessions} 次`"
      >
        <div class="h-full rounded-full bg-care" :style="{ width: `${pct}%` }" />
      </div>
      <p class="mt-1 text-sm text-ink-soft">還有 {{ remaining }} 次</p>

      <dl class="mt-3 space-y-1">
        <div v-if="progress.today_session" class="flex flex-wrap gap-x-2" data-radiation-today>
          <dt class="font-bold">今天</dt>
          <dd>
            第 {{ progress.today_session.number }} 次，{{ formatTime(progress.today_session.scheduled_at, timezone) }}
            <span v-if="progress.today_session.location">・{{ progress.today_session.location }}</span>
            <span v-if="STATUS[progress.today_session.status]" class="ml-1 rounded-full bg-ok-soft px-2 py-0.5 text-sm font-bold text-ok">{{ STATUS[progress.today_session.status] }}</span>
          </dd>
        </div>
        <div v-if="progress.next_session" class="flex flex-wrap gap-x-2" data-radiation-next>
          <dt class="font-bold">下一次</dt>
          <dd>
            第 {{ progress.next_session.number }} 次，{{ formatDate(localDate(progress.next_session.scheduled_at, timezone)) }}
            {{ formatTime(progress.next_session.scheduled_at, timezone) }}
          </dd>
        </div>
      </dl>

      <p v-if="disclaimer" class="mt-3 text-sm text-ink-soft">{{ disclaimer }}</p>
      <RouterLink v-if="detailsTo" :to="detailsTo" class="mt-2 inline-flex min-h-11 items-center font-medium text-care hover:underline">查看我的療程</RouterLink>
    </template>
  </WidgetFrame>
</template>
