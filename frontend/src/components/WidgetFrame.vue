<script setup>
import AppIcon from '@/components/AppIcon.vue'

/**
 * Common frame for every dashboard widget (ui-architecture.md §2.5):
 * header + loading / empty / error states, so widgets only render the happy path.
 */
defineProps({
  title: { type: String, required: true },
  icon: { type: String, default: null },
  state: { type: String, default: 'ready' }, // ready | loading | empty | error
  emptyText: { type: String, default: '目前沒有資料' },
  errorText: { type: String, default: '資料載入失敗' },
  tone: { type: String, default: 'default' }, // default | critical
})
defineEmits(['retry'])
</script>

<template>
  <section
    class="rounded-2xl border bg-surface"
    :class="tone === 'critical' ? 'border-critical/60' : 'border-line'"
  >
    <header class="flex items-center gap-2 border-b border-line px-5 py-3">
      <AppIcon v-if="icon" :name="icon" class="shrink-0 text-care" />
      <h2 class="text-lg font-bold">{{ title }}</h2>
      <div class="ml-auto text-sm text-ink-soft"><slot name="meta" /></div>
    </header>

    <div class="px-5 py-4">
      <div v-if="state === 'loading'" class="space-y-3" aria-busy="true" aria-label="載入中">
        <div class="h-5 w-2/3 animate-pulse rounded bg-mist" />
        <div class="h-5 w-1/2 animate-pulse rounded bg-mist" />
      </div>
      <p v-else-if="state === 'empty'" class="text-ink-soft">{{ emptyText }}</p>
      <div v-else-if="state === 'error'" class="flex items-center justify-between gap-3">
        <p class="text-critical">{{ errorText }}</p>
        <button
          type="button"
          class="min-h-12 rounded-full border border-line px-4 font-medium hover:bg-mist"
          @click="$emit('retry')"
        >
          重新載入
        </button>
      </div>
      <slot v-else />
    </div>
  </section>
</template>
