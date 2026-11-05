<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import ChatThread from '@/components/ChatThread.vue'
import NurseNav from '@/components/NurseNav.vue'
import { usePolling } from '@/composables/usePolling'
import { useChatStore } from '@/stores/chat'

/**
 * 病人訊息 (nurse): the conversations of the patients currently assigned to this nurse (the
 * backend lists only those and answers 404 for anyone else), unread first. Wide screens show the
 * list and the open conversation side by side; phones show one at a time.
 */
const route = useRoute()
const chat = useChatStore()
const patientId = computed(() => route.params.patientId || null)
usePolling(() => chat.fetchInbox(), 60_000)

const current = computed(() => chat.inbox?.find((r) => r.patient.id === patientId.value) ?? null)

const TZ = 'Asia/Taipei'
const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ })
const timeText = new Intl.DateTimeFormat('zh-TW', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const dateText = new Intl.DateTimeFormat('zh-TW', { timeZone: TZ, month: 'numeric', day: 'numeric' })
function when(iso) {
  const d = new Date(iso)
  return dayKey.format(d) === dayKey.format(new Date()) ? timeText.format(d) : dateText.format(d)
}
const PREFIX = { patient: '', nurse: '護理師：', system: '自動回覆：' }
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <NurseNav active="nurse-messages" />

    <main class="min-w-0 lg:grid lg:grid-cols-[20rem_1fr]">
      <section
        class="border-line bg-surface lg:sticky lg:top-0 lg:h-dvh lg:overflow-y-auto lg:border-r"
        :class="patientId ? 'hidden lg:block' : ''"
        aria-labelledby="inbox-title"
        data-chat-inbox
      >
        <div class="border-b border-line p-4">
          <RouterLink :to="{ name: 'nurse' }" class="text-sm text-care hover:underline lg:hidden">← 照護總覽</RouterLink>
          <h1 id="inbox-title" class="text-xl font-bold">病人訊息</h1>
          <p class="text-sm text-ink-soft">您目前負責的病人</p>
        </div>
        <div v-if="chat.inboxError && !chat.inbox" class="p-4" role="alert">
          <p class="font-bold text-critical">{{ chat.inboxError.message }}</p>
          <button type="button" class="mt-3 min-h-11 rounded-full border border-line px-4" @click="chat.fetchInbox()">重新載入</button>
        </div>
        <p v-else-if="!chat.inbox" class="p-4 text-ink-soft" aria-busy="true">載入中…</p>
        <p v-else-if="!chat.inbox.length" class="p-4 text-ink-soft">目前沒有負責的病人。</p>
        <ul v-else>
          <li v-for="row in chat.inbox" :key="row.patient.id" :data-inbox-patient="row.patient.id">
            <RouterLink
              :to="{ name: 'nurse-messages', params: { patientId: row.patient.id } }"
              class="flex min-h-16 items-start gap-3 border-b border-line px-4 py-3 hover:bg-mist"
              :class="row.patient.id === patientId ? 'bg-care-soft' : ''"
              :aria-current="row.patient.id === patientId ? 'page' : undefined"
            >
              <span class="min-w-0 flex-1">
                <span class="flex items-baseline gap-2">
                  <span class="truncate font-bold">{{ row.patient.display_name }}</span>
                  <span class="shrink-0 text-sm text-ink-soft">{{ row.patient.patient_code }}</span>
                  <span v-if="row.last_message" class="ml-auto shrink-0 text-xs text-ink-soft">{{ when(row.last_message.created_at) }}</span>
                </span>
                <span class="mt-0.5 block truncate text-sm" :class="row.unread_count ? 'font-bold text-ink' : 'text-ink-soft'">
                  <template v-if="row.last_message">{{ PREFIX[row.last_message.sender_role] }}{{ row.last_message.body }}</template>
                  <template v-else>尚無訊息</template>
                </span>
              </span>
              <span
                v-if="row.unread_count"
                class="mt-0.5 grid min-w-6 shrink-0 place-items-center rounded-full bg-critical px-1.5 text-sm font-bold text-white"
                :aria-label="`${row.unread_count} 則未讀`"
                data-inbox-unread
              >{{ row.unread_count }}</span>
            </RouterLink>
          </li>
        </ul>
      </section>

      <section v-if="patientId" class="flex min-h-dvh min-w-0 flex-col" aria-labelledby="thread-title">
        <header class="sticky top-0 z-20 flex items-center gap-2 border-b border-line bg-surface/95 px-2 py-2 backdrop-blur">
          <RouterLink :to="{ name: 'nurse-messages' }" class="grid size-12 shrink-0 place-items-center rounded-full text-ink-soft hover:bg-mist lg:hidden" aria-label="回病人訊息列表">
            <AppIcon name="chevron" :size="22" class="rotate-180" />
          </RouterLink>
          <div class="min-w-0 flex-1 px-2">
            <h2 id="thread-title" class="truncate font-bold">{{ current?.patient.display_name ?? '對話' }}</h2>
            <p v-if="current" class="text-sm text-ink-soft">{{ current.patient.patient_code }}</p>
          </div>
          <RouterLink :to="{ name: 'nurse-patient', params: { id: patientId } }" class="min-h-11 shrink-0 content-center rounded-full border border-line px-4 text-sm hover:bg-mist">病人資料</RouterLink>
        </header>
        <ChatThread :patient-id="patientId" viewer-role="nurse" />
      </section>
      <section v-else class="hidden place-items-center p-6 text-ink-soft lg:grid">
        請從左側選擇病人，查看並回覆訊息。
      </section>
    </main>
  </div>
</template>
