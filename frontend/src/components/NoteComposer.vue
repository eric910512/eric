<script setup>
/** Textarea + quick phrases for nurse notes (resolution note / review action note). */
const props = defineProps({
  label: { type: String, required: true },
  placeholder: { type: String, default: '' },
  maxlength: { type: Number, default: 1000 },
  invalid: { type: Boolean, default: false },
})
const model = defineModel({ default: '' })

const PHRASES = ['已電話聯繫病人', '已衛教居家照護', '建議立即前往急診', '已通知主治醫師', '明日電話追蹤']

function add(phrase) {
  const current = model.value.trim()
  model.value = current ? `${current.replace(/[，。]$/, '')}，${phrase}` : phrase
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
    <ul class="mt-2 flex flex-wrap gap-1.5" aria-label="常用句">
      <li v-for="p in PHRASES" :key="p">
        <button type="button" class="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-care-soft" @click="add(p)">
          ＋{{ p }}
        </button>
      </li>
    </ul>
  </div>
</template>
