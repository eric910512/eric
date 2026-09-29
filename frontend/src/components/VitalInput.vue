<script setup>
import { useId } from 'vue'

import { VITAL_FIELDS } from '@/utils/vitals'

/** One numeric vital-sign field: large input with unit, validation error and live range hint. */
const props = defineProps({
  field: { type: String, required: true },
  label: { type: String, default: null },
  error: { type: String, default: null },
  hint: { type: Object, default: null }, // { level, text }
})
const model = defineModel({ default: '' })
const id = useId()
const meta = VITAL_FIELDS[props.field]
</script>

<template>
  <div>
    <label :for="id" class="block font-medium">{{ label ?? meta.label }}</label>
    <div class="mt-1 flex items-center rounded-xl border bg-surface focus-within:border-care"
         :class="error ? 'border-critical' : hint?.level === 'critical' ? 'border-critical' : hint?.level === 'warning' ? 'border-warn' : 'border-line'">
      <input
        :id="id"
        v-model="model"
        type="text"
        :inputmode="meta.decimals ? 'decimal' : 'numeric'"
        autocomplete="off"
        :placeholder="meta.placeholder"
        :aria-invalid="!!error"
        :aria-describedby="`${id}-msg`"
        class="min-h-14 w-full min-w-0 rounded-xl bg-transparent px-4 text-2xl font-bold outline-none placeholder:font-normal placeholder:text-muted"
      />
      <span class="shrink-0 pr-4 text-ink-soft">{{ meta.unit }}</span>
    </div>
    <p :id="`${id}-msg`" class="mt-1 min-h-5 text-sm" aria-live="polite">
      <span v-if="error" class="font-medium text-critical">{{ error }}</span>
      <span v-else-if="hint" class="font-bold" :class="hint.level === 'critical' ? 'text-critical' : 'text-warn'">
        {{ hint.text }}{{ hint.level === 'critical' ? '（危急）' : '' }}
      </span>
    </p>
  </div>
</template>
