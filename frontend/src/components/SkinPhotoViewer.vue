<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import SkinPhoto from '@/components/SkinPhoto.vue'
import { formatTime, localDate } from '@/utils/format'

/** Larger view of one skin photo with its upload date, body site and the patient's answers. Esc closes. */
defineProps({
  photo: { type: Object, required: true }, // photo payload (+ optional record: { recorded_at, answers, notes })
  answers: { type: String, default: '' },
  notes: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const TZ = 'Asia/Taipei'
const closeBtn = ref(null)
const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
const onKey = (e) => e.key === 'Escape' && emit('close')
onMounted(async () => {
  addEventListener('keydown', onKey)
  await nextTick()
  closeBtn.value?.focus()
})
onBeforeUnmount(() => removeEventListener('keydown', onKey))
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-ink/70 p-3" role="dialog" aria-modal="true" aria-label="皮膚照片" data-skin-viewer @click.self="emit('close')">
    <div class="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-surface">
      <div class="flex items-center gap-2 border-b border-line px-4 py-2">
        <p class="min-w-0 flex-1 text-sm">
          <span class="font-bold">上傳時間 {{ when(photo.created_at) }}</span>
          <span class="ml-2 rounded-full px-2 py-0.5" :class="photo.body_site_label ? 'bg-care-soft text-care' : 'bg-warn-soft text-warn'"
            data-skin-viewer-site>{{ photo.body_site_label ?? '位置未指定' }}</span>
        </p>
        <button ref="closeBtn" type="button" class="min-h-11 rounded-full px-4 hover:bg-mist" data-skin-viewer-close @click="emit('close')">關閉</button>
      </div>
      <div class="min-h-0 flex-1 overflow-auto bg-ink/5">
        <SkinPhoto :photo-id="photo.id" variant="full" img-class="mx-auto max-h-[70dvh] w-auto max-w-full object-contain" alt="皮膚照片（較大尺寸）" />
      </div>
      <div v-if="answers || notes" class="space-y-1 border-t border-line px-4 py-3 text-sm" data-skin-viewer-answers>
        <p v-if="answers"><span class="font-bold">病人填寫的症狀：</span>{{ answers }}</p>
        <p v-if="notes" class="whitespace-pre-line"><span class="font-bold">補充說明：</span>{{ notes }}</p>
      </div>
      <p class="border-t border-line px-4 py-2 text-xs text-ink-soft">照片僅供照護參考，系統不會自動判讀嚴重程度，也不會判斷改善或惡化。</p>
    </div>
  </div>
</template>
