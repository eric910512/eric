<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'

import { useSkinPhotoStore } from '@/stores/skinPhotos'
import { ACCEPT, prepareImage } from '@/utils/imagePrep'

/**
 * Photos of one skin self-assessment (optional, at most `max`): take or choose → preview → each photo is
 * prepared in the browser and uploaded on its own (pending until the report is sent, Idempotency-Key per
 * photo, kept for a retry) → status and reason per photo → retry / remove / choose again, body site.
 * The model is the list of items; the page sends `{ id, body_site }` of the uploaded ones with the report.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  max: { type: Number, default: 3 },
  bodySites: { type: Array, default: () => [] },
})
const items = defineModel({ type: Array, required: true })
const store = useSkinPhotoStore()
const camera = ref(null)
const library = ref(null)
const notice = ref('')
let seq = 0

const remaining = computed(() => props.max - items.value.length)
const STATUS = { preparing: '處理中…', uploading: '上傳中…', uploaded: '已上傳（送出後才會給護理師）', failed: '上傳失敗', invalid: '無法使用' }
const newKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`

function open(input) {
  notice.value = ''
  if (remaining.value <= 0) {
    notice.value = `每次最多 ${props.max} 張照片。`
    return
  }
  input.value?.click()
}

async function onPick(event) {
  const files = [...(event.target.files ?? [])]
  event.target.value = '' // the same file can be chosen again
  if (files.length > remaining.value) notice.value = `每次最多 ${props.max} 張照片，只加入前 ${Math.max(remaining.value, 0)} 張。`
  for (const file of files.slice(0, Math.max(remaining.value, 0))) {
    items.value.push({ localId: ++seq, status: 'preparing', error: null, previewUrl: null, prepared: null, photoId: null, key: null, site: '' })
    const item = items.value.at(-1) // the reactive copy
    try {
      item.prepared = await prepareImage(file)
      item.previewUrl = URL.createObjectURL(item.prepared.thumbBlob)
    } catch (err) {
      item.status = 'invalid'
      item.error = err.message || '無法讀取這張照片'
      continue
    }
    upload(item)
  }
}

async function upload(item) {
  item.status = 'uploading'
  item.error = null
  item.key ??= newKey()
  try {
    const photo = await store.upload(props.patientId, item.prepared, item.key)
    if (!items.value.includes(item)) return // removed while uploading
    item.photoId = photo.id
    item.status = 'uploaded'
  } catch (err) {
    item.status = 'failed'
    item.error = err.code === 'NETWORK' ? '網路不穩定，照片還沒上傳。' : (err.message || '照片上傳失敗')
    const retryable = !err.status || err.status >= 500 || err.code === 'IDEMPOTENCY_IN_PROGRESS'
    if (!retryable) item.key = null // a rejected upload is a new request next time
  }
}

async function remove(item) {
  const i = items.value.indexOf(item)
  if (i >= 0) items.value.splice(i, 1)
  if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
  if (item.photoId) {
    try {
      await store.discard(item.photoId)
    } catch { /* already gone; an unsent photo is cleaned up by the server later anyway */ }
  }
}

function again(item) {
  remove(item)
  open(library)
}

onBeforeUnmount(() => {
  for (const item of items.value) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
})
</script>

<template>
  <div class="space-y-3" data-photo-picker>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="inline-flex min-h-12 items-center gap-2 rounded-full border border-care px-4 font-bold text-care hover:bg-care-soft"
        data-photo-camera @click="open(camera)">拍照</button>
      <button type="button" class="inline-flex min-h-12 items-center gap-2 rounded-full border border-line px-4 font-bold hover:bg-mist"
        data-photo-library @click="open(library)">從相簿選擇</button>
      <input ref="camera" type="file" :accept="ACCEPT" capture="environment" class="sr-only" tabindex="-1" aria-hidden="true" data-photo-input-camera @change="onPick" />
      <input ref="library" type="file" :accept="ACCEPT" multiple class="sr-only" tabindex="-1" aria-hidden="true" data-photo-input-library @change="onPick" />
    </div>
    <p class="text-sm text-ink-soft">選填，最多 {{ max }} 張；支援 JPEG、PNG、WebP。iPhone 的 HEIC 格式目前不支援，若無法加入請改用「最相容」格式。</p>
    <p v-if="notice" class="rounded-xl bg-warn-soft px-3 py-2 text-sm text-warn" role="status" data-photo-notice>{{ notice }}</p>

    <ul v-if="items.length" class="space-y-2">
      <li v-for="item in items" :key="item.localId" class="flex gap-3 rounded-xl border p-2"
        :class="item.status === 'failed' || item.status === 'invalid' ? 'border-critical/50 bg-critical-soft' : 'border-line'"
        :data-photo-item="item.status">
        <div class="size-20 shrink-0 overflow-hidden rounded-lg bg-mist">
          <img v-if="item.previewUrl" :src="item.previewUrl" alt="照片預覽" class="h-full w-full object-cover" data-photo-preview />
        </div>
        <div class="min-w-0 flex-1 space-y-1">
          <p class="text-sm font-bold" :class="item.status === 'uploaded' ? 'text-ok' : item.status === 'failed' || item.status === 'invalid' ? 'text-critical' : 'text-ink-soft'"
            data-photo-status>{{ STATUS[item.status] }}</p>
          <p v-if="item.error" class="text-sm text-critical" role="alert" data-photo-error>{{ item.error }}</p>
          <label v-if="item.status !== 'invalid' && bodySites.length" class="flex flex-wrap items-center gap-1 text-sm">
            部位（選填）
            <select v-model="item.site" class="min-h-9 max-w-full rounded-lg border border-line bg-surface px-1" data-photo-site>
              <option value="">未選擇</option>
              <option v-for="s in bodySites" :key="s.code" :value="s.code">{{ s.label }}</option>
            </select>
          </label>
          <div class="flex flex-wrap gap-1">
            <button v-if="item.status === 'failed'" type="button" class="min-h-10 rounded-full bg-care px-3 text-sm font-bold text-white" data-photo-retry @click="upload(item)">重試</button>
            <button v-if="item.status !== 'preparing' && item.status !== 'uploading'" type="button" class="min-h-10 rounded-full border border-line px-3 text-sm hover:bg-mist"
              data-photo-again @click="again(item)">重新選擇</button>
            <button v-if="item.status !== 'preparing'" type="button" class="min-h-10 rounded-full border border-line px-3 text-sm hover:bg-mist" data-photo-remove @click="remove(item)">移除</button>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>
