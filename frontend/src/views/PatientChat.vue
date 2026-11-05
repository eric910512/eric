<script setup>
import { computed } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import ChatThread from '@/components/ChatThread.vue'
import { useChatStore } from '@/stores/chat'

/**
 * 聊天室 (patient): the patient's own conversation with the 放射腫瘤科 nurses (`me` — the backend
 * never lets a patient open another one). The service hours and the phone number stay on top.
 * Text only: no photos, voice or attachments in this version.
 */
const chat = useChatStore()
// shown before the first response arrives (the API repeats the same fixed information)
const FALLBACK = { name: '義大醫院放射腫瘤科', hours_text: '星期一至五 09:00-12:30、13:30-17:30', phone: '07-6150011', extension: '2205', phone_text: '07-6150011 分機 2205' }
const service = computed(() => chat.thread?.service ?? FALLBACK)
const EMERGENCY = '本聊天室非即時緊急醫療服務。如有呼吸困難、意識不清等緊急狀況，請立即撥打 119 或就近急診就醫。'
const tel = computed(() => `tel:${service.value.phone.replace(/-/g, '')},${service.value.extension}`)
</script>

<template>
  <div class="flex min-h-dvh flex-col">
    <header class="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="grid size-12 shrink-0 place-items-center rounded-full text-ink-soft hover:bg-mist hover:text-ink" aria-label="回首頁">
          <AppIcon name="chevron" :size="22" class="rotate-180" />
        </RouterLink>
        <div class="min-w-0">
          <h1 class="font-bold leading-tight">聊天室</h1>
          <p class="truncate text-sm text-ink-soft">{{ service.name }}・放射腫瘤科護理師</p>
        </div>
      </div>
      <div class="mx-auto max-w-xl px-4 pb-2.5" data-chat-service>
        <p class="rounded-xl bg-care-soft px-3 py-2 text-sm leading-relaxed">
          <span class="font-bold">服務時間</span>：{{ service.hours_text }}<br />
          <span class="font-bold">聯絡電話</span>：<a :href="tel" class="font-bold text-care underline underline-offset-2">{{ service.phone_text }}</a>
        </p>
        <!-- fixed notice on the page, never a chat message (the daily auto-reply text stays as it is) -->
        <p class="mt-1.5 flex gap-1.5 rounded-xl bg-critical-soft px-3 py-1.5 text-xs leading-snug text-critical" role="note" data-chat-emergency>
          <AppIcon name="alert" :size="16" class="mt-px shrink-0" />
          <span>{{ EMERGENCY }}</span>
        </p>
      </div>
    </header>

    <div class="mx-auto flex w-full max-w-xl flex-1 flex-col">
      <ChatThread patient-id="me" viewer-role="patient" />
    </div>
  </div>
</template>
