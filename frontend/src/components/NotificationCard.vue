<script setup>
import AppIcon from '@/components/AppIcon.vue'
import WidgetFrame from '@/components/WidgetFrame.vue'
import { formatWhen } from '@/utils/format'

/**
 * my-notifications widget: latest notifications for the signed-in patient. Risk alerts show
 * how the care team is handling them (status only — staff notes are never sent to patients).
 */
defineProps({
  notifications: { type: Object, required: true },
  today: { type: String, required: true },
  timezone: { type: String, default: 'Asia/Taipei' },
})

const STATUS = {
  new: 'bg-critical-soft text-critical',
  acknowledged: 'bg-care-soft text-care',
  in_progress: 'bg-warn-soft text-warn',
  resolved: 'bg-ok-soft text-ok',
}

const SEVERITY = {
  critical: { icon: 'alert', box: 'border-critical bg-critical-soft', ink: 'text-critical' },
  warning: { icon: 'alert', box: 'border-warn/50 bg-warn-soft', ink: 'text-warn' },
  info: { icon: 'bell', box: 'border-line', ink: 'text-care' },
}
</script>

<template>
  <WidgetFrame
    title="我的通知"
    icon="bell"
    :state="notifications.items.length ? 'ready' : 'empty'"
    empty-text="目前沒有新通知。"
  >
    <template #meta>
      <span v-if="notifications.unread_count">{{ notifications.unread_count }} 則未讀</span>
    </template>

    <ul class="space-y-3">
      <li
        v-for="n in notifications.items"
        :key="n.id"
        class="flex gap-3 rounded-xl border px-4 py-3"
        :class="SEVERITY[n.severity]?.box ?? SEVERITY.info.box"
      >
        <AppIcon :name="SEVERITY[n.severity]?.icon ?? 'bell'" class="mt-0.5 shrink-0" :class="SEVERITY[n.severity]?.ink" />
        <div class="min-w-0 flex-1">
          <p class="flex items-start gap-2">
            <span class="font-bold" :class="n.severity === 'critical' ? 'text-critical' : ''">{{ n.title }}</span>
            <span v-if="!n.is_read" class="mt-2 size-2 shrink-0 rounded-full bg-critical" aria-label="未讀" />
          </p>
          <p class="mt-0.5">{{ n.message }}</p>
          <p class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-soft">
            {{ formatWhen(n.created_at, today, timezone) }}
            <span v-if="n.status_text" class="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-bold" :class="STATUS[n.status]" data-status>
              <AppIcon v-if="n.status === 'resolved'" name="check" :size="14" />{{ n.status_text }}
            </span>
          </p>
        </div>
      </li>
    </ul>
  </WidgetFrame>
</template>
