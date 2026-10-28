<script setup>
/**
 * Textarea + 處理項目 (quick phrases) for nurse notes (resolution note / review action note).
 * The note stays internal: patients only see the status (e.g. 已處理完成), never this text.
 */
const props = defineProps({
  label: { type: String, required: true },
  placeholder: { type: String, default: '' },
  maxlength: { type: Number, default: 1000 },
  invalid: { type: Boolean, default: false },
})
const model = defineModel({ default: '' })

const PHRASES = ['已協助掛號，請明日至門診報到。', '建議立即前往急診。', '已給予護理衛教。', '已電話聯繫病人。']

function add(phrase) {
  const current = model.value.trim()
  if (current.includes(phrase)) return // already chosen
  model.value = current ? `${current.replace(/[，。]$/, '')}。${phrase}` : phrase
}
</script>

<template>
  <div>
    <label class="block font-medium">
      {{ props.label }}
      <textarea
        v-model="model"
        rows="3"
        :maxlength="maxlength"
        :placeholder="placeholder"
        :aria-invalid="invalid"
        class="mt-1 block w-full rounded-xl border bg-surface px-3 py-2 text-base font-normal focus:border-care"
        :class="invalid ? 'border-critical' : 'border-line'"
      />
    </label>
    <p class="mt-2 text-sm font-medium text-ink-soft" aria-hidden="true">處理項目</p>
    <ul class="mt-1 flex flex-wrap gap-1.5" aria-label="處理項目" data-handling-items>
      <li v-for="p in PHRASES" :key="p">
        <button
          type="button"
          class="min-h-11 max-w-full rounded-full border px-3 py-1 text-left text-sm"
          :class="model.includes(p) ? 'border-care bg-care-soft font-bold text-care' : 'border-line hover:bg-care-soft'"
          :aria-pressed="model.includes(p)"
          :data-handling-item="p"
          @click="add(p)"
        >
          {{ model.includes(p) ? '✓' : '＋' }}{{ p }}
        </button>
      </li>
    </ul>
  </div>
</template>
