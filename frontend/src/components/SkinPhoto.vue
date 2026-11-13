<script setup>
import { ref, watch } from 'vue'

import { useSkinPhotoStore } from '@/stores/skinPhotos'

/**
 * One skin photo, loaded with the signed-in user's token (GET /symptom-photos/{id}/content; the backend
 * checks access on every request). No public URL: the image is shown from an object URL.
 */
const props = defineProps({
  photoId: { type: String, required: true },
  variant: { type: String, default: 'thumb' }, // thumb | full
  alt: { type: String, default: '皮膚照片' },
  imgClass: { type: String, default: 'h-full w-full object-cover' },
})
const store = useSkinPhotoStore()
const url = ref(null)
const failed = ref(false)

watch(() => [props.photoId, props.variant], async ([id, variant]) => {
  url.value = null
  failed.value = false
  try {
    url.value = await store.imageUrl(id, variant)
  } catch {
    failed.value = true
  }
}, { immediate: true })
</script>

<template>
  <img v-if="url" :src="url" :alt="alt" :class="imgClass" draggable="false" data-skin-photo-img />
  <span v-else-if="failed" class="grid h-full w-full place-items-center bg-mist p-1 text-center text-xs text-ink-soft" data-skin-photo-error>照片無法載入</span>
  <span v-else class="block h-full w-full animate-pulse bg-mist" aria-busy="true" />
</template>
