<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { usePolling } from '@/composables/usePolling'
import { useChatStore } from '@/stores/chat'

/**
 * One conversation, LINE style (patient page and nurse page). The viewer's side is on the right,
 * the other side on the left with its label (patients see 放射腫瘤科護理師), the system auto reply in
 * its own style and never as a nurse's reply. Date separators and times are Asia/Taipei.
 * New messages are polled every ~10 s while the page is in the foreground; the page scrolls with
 * the window and the composer stays at the bottom of the screen.
 */
const props = defineProps({
  patientId: { type: String, required: true }, // 'me' for the patient
  viewerRole: { type: String, required: true }, // patient | nurse
})

const chat = useChatStore()
const t = computed(() => (chat.thread?.patientId === props.patientId ? chat.thread : null))

const TZ = 'Asia/Taipei'
const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ })
const dayText = new Intl.DateTimeFormat('zh-TW', { timeZone: TZ, month: 'long', day: 'numeric', weekday: 'short' })
const timeText = new Intl.DateTimeFormat('zh-TW', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const time = (iso) => timeText.format(new Date(iso))
function dayLabel(iso, key) {
  const today = dayKey.format(new Date())
  const yesterday = dayKey.format(new Date(Date.now() - 86_400_000))
  return key === today ? '今天' : key === yesterday ? '昨天' : dayText.format(new Date(iso))
}

const rows = computed(() => {
  if (!t.value) return []
  const items = [
    ...t.value.messages.map((m) => ({ ...m, key: `m-${m.id}` })),
    ...t.value.pending.map((p) => ({ key: `p-${p.localId}`, body: p.body, created_at: p.created_at, sender_role: props.viewerRole, kind: 'text', is_mine: true, pending: p })),
  ]
  const out = []
  let day = null
  for (const m of items) {
    const key = dayKey.format(new Date(m.created_at))
    if (key !== day) {
      day = key
      out.push({ type: 'day', key: `d-${key}`, label: dayLabel(m.created_at, key) })
    }
    out.push({ type: m.kind === 'auto_reply' ? 'system' : m.sender_role === props.viewerRole ? 'own' : 'other', key: m.key, m })
  }
  return out
})

// ------------------------------------------------------------------ loading / polling
usePolling(() => (t.value ? chat.fetchNewer() : chat.openThread(props.patientId)), 10_000)
watch(() => props.patientId, (id) => chat.openThread(id))
onBeforeUnmount(() => chat.closeThread())

// a first request to a sleeping server can take about a minute: say so instead of looking stuck
const slow = ref(false)
let slowTimer = null
watch(() => t.value?.loading, (loading) => {
  clearTimeout(slowTimer)
  slow.value = false
  if (loading) slowTimer = setTimeout(() => { slow.value = true }, 4000)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(slowTimer))

// ------------------------------------------------------------------ scrolling (the window scrolls)
const root = document.documentElement
const nearBottom = () => innerHeight + scrollY >= root.scrollHeight - 160
const toBottom = () => window.scrollTo({ top: root.scrollHeight })
// newest message / pending count: follow new messages only when the reader was already at the
// bottom (or just sent one); reading older messages is never interrupted
const tail = () => (t.value ? `${t.value.messages.at(-1)?.id ?? 0}/${t.value.pending.length}` : '')
let wasNear = true
watch(tail, () => { wasNear = nearBottom() }, { flush: 'pre' })
watch(tail, (now, before) => {
  if (!now) return
  const sent = Number(now.split('/')[1]) > Number((before || '0/0').split('/')[1])
  if (!before || wasNear || sent) toBottom()
}, { flush: 'post' })

async function loadOlder() {
  const before = root.scrollHeight
  await chat.fetchOlder()
  await nextTick()
  window.scrollBy({ top: root.scrollHeight - before })
}

// ------------------------------------------------------------------ composer
const draft = ref('')
const box = ref(null)
const MAX = 1000
const finePointer = typeof matchMedia === 'function' && matchMedia('(pointer: fine)').matches

function grow() {
  const el = box.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}
async function submit() {
  const text = draft.value.trim()
  if (!text || !t.value) return
  draft.value = ''
  nextTick(grow)
  await chat.send(text)
}
function onKeydown(e) {
  // keyboard (desktop): Enter sends, Shift+Enter is a new line. Never while an IME (注音 / 倉頡) is
  // composing. Touch keyboards: Enter is a new line; the send button sends.
  if (e.key === 'Enter' && !e.shiftKey && finePointer && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault()
    submit()
  }
}
</script>

<template>
  <div class="flex flex-1 flex-col" data-chat-thread>
    <div class="flex-1 px-4 py-3">
      <!-- first load -->
      <div v-if="!t || (t.loading && !t.loaded)" class="py-10 text-center text-ink-soft" aria-busy="true" role="status">
        <p>正在載入訊息…</p>
        <p v-if="slow" class="mx-auto mt-2 max-w-xs text-sm" data-chat-slow>伺服器可能正在喚醒，第一次連線約需一分鐘，請稍候。</p>
      </div>
      <div v-else-if="t.error && !t.loaded" class="rounded-2xl border border-critical/40 bg-surface p-5 text-center" role="alert" data-chat-error>
        <p class="font-bold text-critical">{{ t.error.message || '無法載入訊息' }}</p>
        <button type="button" class="mt-3 min-h-12 rounded-full border border-line px-5" @click="chat.openThread(patientId)">重新載入</button>
      </div>

      <template v-else>
        <div v-if="t.hasMore" class="mb-3 text-center">
          <button type="button" class="min-h-11 rounded-full border border-line bg-surface px-4 text-sm text-ink-soft hover:text-ink" :disabled="t.loadingOlder" data-chat-older @click="loadOlder">
            {{ t.loadingOlder ? '載入中…' : '載入更早的訊息' }}
          </button>
        </div>
        <p v-if="!rows.length" class="py-10 text-center text-ink-soft" data-chat-empty>還沒有訊息。有放射線治療照護上的問題，歡迎在下方輸入。</p>

        <ol class="space-y-3" role="log" aria-live="polite" aria-label="聊天訊息" data-chat-messages>
          <li v-for="row in rows" :key="row.key" :data-message-id="row.m?.id" :data-side="row.type">
            <p v-if="row.type === 'day'" class="flex justify-center">
              <span class="rounded-full bg-ink/10 px-3 py-0.5 text-sm text-ink-soft">{{ row.label }}</span>
            </p>

            <div v-else-if="row.type === 'system'" class="mr-6 rounded-2xl border border-dashed border-care/50 bg-care-soft p-3.5" data-chat-system>
              <p class="mb-1.5 flex items-center gap-1.5 text-sm font-bold text-care">
                <AppIcon name="chat" :size="16" /> 系統自動回覆
              </p>
              <p class="whitespace-pre-line break-words">{{ row.m.body }}</p>
              <p class="mt-2 text-xs text-ink-soft">此為系統自動發送的訊息，並非護理師本人回覆 · <time :datetime="row.m.created_at">{{ time(row.m.created_at) }}</time></p>
            </div>

            <div v-else-if="row.type === 'own'" class="flex flex-col items-end">
              <span v-if="!row.m.is_mine" class="mb-0.5 text-sm text-ink-soft">{{ row.m.sender_label }}</span>
              <div class="flex max-w-full items-end justify-end gap-1.5">
                <time v-if="!row.m.pending" :datetime="row.m.created_at" class="shrink-0 text-xs text-ink-soft">{{ time(row.m.created_at) }}</time>
                <p
                  class="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md px-3.5 py-2"
                  :class="[row.m.is_mine ? 'bg-care text-white' : 'bg-care-soft text-ink', row.m.pending ? 'opacity-70' : '']"
                >{{ row.m.body }}</p>
              </div>
              <p v-if="row.m.pending?.status === 'sending'" class="mt-0.5 text-xs text-ink-soft" data-chat-sending>傳送中…</p>
              <div v-else-if="row.m.pending?.status === 'failed'" class="mt-1 flex flex-wrap items-center justify-end gap-2 text-sm" role="alert" data-chat-failed>
                <span class="text-critical">傳送失敗：{{ row.m.pending.error }}</span>
                <button type="button" class="min-h-10 rounded-full border border-critical/50 px-3 font-bold text-critical" @click="chat.retry(row.m.pending.localId)">重新傳送</button>
                <button type="button" class="min-h-10 rounded-full px-3 text-ink-soft hover:text-ink" @click="chat.discard(row.m.pending.localId)">刪除</button>
              </div>
            </div>

            <div v-else class="flex flex-col items-start" data-chat-other>
              <span class="mb-0.5 text-sm text-ink-soft">{{ row.m.sender_label }}</span>
              <div class="flex max-w-full items-end gap-1.5">
                <p class="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl rounded-bl-md border border-line bg-surface px-3.5 py-2">{{ row.m.body }}</p>
                <time :datetime="row.m.created_at" class="shrink-0 text-xs text-ink-soft">{{ time(row.m.created_at) }}</time>
              </div>
            </div>
          </li>
        </ol>
      </template>
    </div>

    <form class="sticky bottom-0 z-10 border-t border-line bg-surface px-3 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))]" data-chat-composer @submit.prevent="submit">
      <p v-if="t?.pollError" class="mb-1.5 text-center text-sm text-warn" role="status" data-chat-reconnecting>連線不穩定，正在重新連線…</p>
      <div class="flex items-end gap-2">
        <label for="chat-input" class="sr-only">輸入訊息</label>
        <textarea
          id="chat-input"
          ref="box"
          v-model="draft"
          rows="1"
          :maxlength="MAX"
          placeholder="輸入訊息…"
          class="max-h-40 min-h-12 min-w-0 flex-1 resize-none rounded-2xl border border-line bg-mist px-4 py-2.5"
          :disabled="!t?.loaded"
          @input="grow"
          @keydown="onKeydown"
        />
        <button
          type="submit"
          class="grid size-12 shrink-0 place-items-center rounded-full bg-care text-white disabled:opacity-40"
          :disabled="!draft.trim() || !t?.loaded"
          aria-label="送出訊息"
        >
          <AppIcon name="send" :size="22" />
        </button>
      </div>
      <p v-if="draft.length > MAX - 100" class="mt-1 text-right text-xs text-ink-soft">{{ draft.length }} / {{ MAX }}</p>
    </form>
  </div>
</template>
