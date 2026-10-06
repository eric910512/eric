<script setup>
import { computed, useId } from 'vue'

/**
 * One question of a dynamic symptom form, rendered from its definition
 * (ui-architecture.md §2.4 DynamicFormField). Supports scale / numeric-as-scale / boolean /
 * single choice (options in their defined order). ``hint`` shows a reminder under the answer.
 * Uses native radio inputs so keyboard and screen readers work without extra ARIA.
 */
const props = defineProps({
  definition: { type: Object, required: true },
  required: { type: Boolean, default: false },
  invalid: { type: Boolean, default: false },
  hint: { type: String, default: '' }, // e.g. a self-care reminder; never blocks submitting
})
const model = defineModel({ default: null })

const id = useId()
const scalePoints = computed(() => {
  const d = props.definition
  const step = d.step || 1
  const count = Math.round((d.max_value - d.min_value) / step) + 1
  return Array.from({ length: count }, (_, i) => d.min_value + i * step)
})
</script>

<template>
  <fieldset class="min-w-0" :aria-invalid="invalid" :aria-describedby="definition.help_text ? `${id}-help` : undefined">
    <legend class="text-lg font-bold">
      {{ definition.question_text || definition.name_zh }}
      <span v-if="required" class="sr-only">（必填）</span>
    </legend>
    <p v-if="definition.help_text" :id="`${id}-help`" class="mt-0.5 text-ink-soft">{{ definition.help_text }}</p>

    <!-- 0–10 scale -->
    <div v-if="definition.value_type === 'scale' || definition.value_type === 'numeric'" class="mt-3">
      <div class="grid grid-cols-6 gap-1.5 sm:grid-cols-11">
        <label v-for="n in scalePoints" :key="n" class="relative">
          <input v-model="model" type="radio" :name="id" :value="n" class="peer sr-only" />
          <span
            class="grid min-h-12 cursor-pointer place-items-center rounded-lg border text-lg font-bold transition-colors
                   peer-focus-visible:outline-3 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-care"
            :class="model === n
              ? (n >= 7 ? 'border-action-ink bg-action-ink text-white' : 'border-care bg-care text-white')
              : 'border-line bg-surface hover:bg-care-soft'"
          >{{ n }}</span>
        </label>
      </div>
      <div class="mt-1 flex justify-between text-sm text-ink-soft" aria-hidden="true">
        <span>{{ definition.min_value }} {{ definition.min_label }}</span>
        <span>{{ definition.max_value }} {{ definition.max_label }}</span>
      </div>
    </div>

    <!-- yes / no -->
    <div v-else-if="definition.value_type === 'boolean'" class="mt-3 grid grid-cols-2 gap-2">
      <label v-for="opt in [{ v: false, t: '沒有' }, { v: true, t: '有' }]" :key="String(opt.v)" class="relative">
        <input v-model="model" type="radio" :name="id" :value="opt.v" class="peer sr-only" />
        <span
          class="grid min-h-12 cursor-pointer place-items-center rounded-lg border text-lg font-bold transition-colors
                 peer-focus-visible:outline-3 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-care"
          :class="model === opt.v
            ? (opt.v ? 'border-critical bg-critical text-white' : 'border-care bg-care text-white')
            : 'border-line bg-surface hover:bg-care-soft'"
        >{{ opt.t }}</span>
      </label>
    </div>

    <!-- single choice: one button per option, in the defined order -->
    <div v-else-if="definition.value_type === 'single_choice'" class="mt-3 grid gap-2" :class="definition.options.length <= 2 ? 'grid-cols-2' : 'grid-cols-1 sm:grid-cols-2'">
      <label v-for="opt in definition.options" :key="opt.value_code" class="relative">
        <input v-model="model" type="radio" :name="id" :value="opt.value_code" class="peer sr-only" />
        <span
          class="grid min-h-12 cursor-pointer place-items-center rounded-lg border px-3 py-2 text-center text-lg font-bold transition-colors
                 peer-focus-visible:outline-3 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-care"
          :class="model === opt.value_code ? 'border-care bg-care text-white' : 'border-line bg-surface hover:bg-care-soft'"
        >{{ opt.label_zh }}</span>
      </label>
    </div>

    <p v-else class="mt-2 text-ink-soft">此題型尚未支援。</p>
    <p v-if="hint" class="mt-2 rounded-xl bg-warn-soft px-3 py-2 text-warn" role="status" data-field-hint>{{ hint }}</p>
  </fieldset>
</template>
