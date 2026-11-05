<script setup>
import AppIcon from '@/components/AppIcon.vue'
import { usePolling } from '@/composables/usePolling'
import { useChatStore } from '@/stores/chat'

/**
 * 聊天室 button, fixed bottom-right on every patient page (rendered by PatientBottomNav): above the
 * bottom navigation and the iPhone home indicator, aligned to the page column. Unread nurse
 * replies are polled about once a minute while the page is in the foreground.
 */
const chat = useChatStore()
usePolling(() => chat.fetchUnread(), 60_000)
</script>

<template>
  <div class="pointer-events-none fixed inset-x-0 z-20 bottom-[calc(4.75rem+env(safe-area-inset-bottom))]" data-chat-fab>
    <div class="mx-auto flex max-w-xl justify-end px-4">
      <RouterLink
        :to="{ name: 'patient-chat' }"
        class="pointer-events-auto relative flex size-16 flex-col items-center justify-center gap-0.5 rounded-full bg-care text-white shadow-lg ring-4 ring-surface hover:bg-care/90"
        :aria-label="chat.unread ? `聊天室，${chat.unread} 則未讀` : '聊天室'"
      >
        <AppIcon name="chat" :size="24" />
        <span class="text-xs font-bold leading-none" aria-hidden="true">聊天室</span>
        <span
          v-if="chat.unread"
          class="absolute -top-1 -right-1 grid min-w-6 place-items-center rounded-full bg-critical px-1.5 text-sm font-bold text-white ring-2 ring-surface"
          aria-hidden="true"
          data-chat-unread
        >{{ chat.unread > 99 ? '99+' : chat.unread }}</span>
      </RouterLink>
    </div>
  </div>
</template>
