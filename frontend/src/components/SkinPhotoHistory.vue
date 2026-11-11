<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import SkinPhoto from '@/components/SkinPhoto.vue'
import SkinPhotoViewer from '@/components/SkinPhotoViewer.vue'
import { useSkinPhotoStore } from '@/stores/skinPhotos'
import { formatShortDate, formatTime, localDate } from '@/utils/format'

/**
 * 皮膚照片紀錄: the sent photos of one patient, newest first, grouped by date, each with its time and body site
 * (「位置未指定」 when the patient chose none). A side-by-side comparison of two reports (time points) **of one
 * body site only** — different sites are never treated as the same course, and photos without a site form
 * their own group. Patient: own photos; nurse: a currently assigned patient (the backend decides).
 * No automatic grading and no "better / worse" judgement.
 */
const props = defineProps({
  patientId: { type: String, required: true },
  title: { type: String, default: '皮膚照片紀錄' },
})
const store = useSkinPhotoStore()
const TZ = 'Asia/Taipei'
const UNSPECIFIED = 'unspecified'
const site = ref('') // list filter ('' = all)
const compare = ref(false)
const compareSite = ref(null) // the one body site being compared
const pick = reactive({ left: null, right: null }) // record ids of the two compared time points
const SIDES = [{ key: 'left', label: '比較：較早的時間點' }, { key: 'right', label: '比較：另一個時間點' }]
const viewing = ref(null)

const data = computed(() => store.history[props.patientId])
const error = computed(() => store.historyError[props.patientId])
const all = computed(() => data.value?.items ?? [])
const siteKey = (p) => p.body_site ?? UNSPECIFIED
const siteLabel = (p) => p.body_site_label ?? '位置未指定'
const when = (iso) => `${localDate(iso, TZ)} ${formatTime(iso, TZ)}`
const short = (iso) => `${formatShortDate(localDate(iso, TZ))} ${formatTime(iso, TZ)}` // fits two pickers at 390 px

// sites present (newest first), 「位置未指定」 included as its own group
const sites = computed(() => {
  const out = []
  for (const p of all.value) if (!out.find((s) => s.key === siteKey(p))) out.push({ key: siteKey(p), label: siteLabel(p) })
  return out
})
const items = computed(() => (site.value ? all.value.filter((p) => siteKey(p) === site.value) : all.value))
const groups = computed(() => {
  const out = []
  for (const p of items.value) {
    let g = out.find((x) => x.date === p.local_date)
    if (!g) out.push((g = { date: p.local_date, photos: [] }))
    g.photos.push(p)
  }
  return out
})
// time points of one site = its reports (several on the same day stay apart), oldest first
function pointsOf(key) {
  const byRecord = new Map()
  for (const p of all.value.filter((x) => siteKey(x) === key)) {
    if (!byRecord.has(p.record.id)) byRecord.set(p.record.id, { id: p.record.id, at: p.record.recorded_at, record: p.record, photos: [] })
    byRecord.get(p.record.id).photos.push(p)
  }
  return [...byRecord.values()].sort((a, b) => Date.parse(a.at) - Date.parse(b.at))
}
const comparable = computed(() => sites.value.some((s) => pointsOf(s.key).length >= 2))
const points = computed(() => (compareSite.value ? pointsOf(compareSite.value) : []))
const compareLabel = computed(() => sites.value.find((s) => s.key === compareSite.value)?.label ?? '')
const pointById = (id) => points.value.find((p) => p.id === id) ?? null

function toggleCompare() {
  compare.value = !compare.value
  if (compare.value) {
    // the site being looked at, else the newest photo's site that has two time points
    compareSite.value = site.value || sites.value.find((s) => pointsOf(s.key).length >= 2)?.key || sites.value[0]?.key || null
  }
}

function load() {
  store.fetchHistory(props.patientId)
}
watch(() => props.patientId, load, { immediate: true })
watch(points, (pts) => {
  if (!pts.find((p) => p.id === pick.left)) pick.left = pts[0]?.id ?? null
  // by default the newest; also when both sides ended up on the same point (e.g. a new report arrived)
  if (!pts.find((p) => p.id === pick.right) || (pick.right === pick.left && pts.length > 1)) pick.right = pts.at(-1)?.id ?? null
})
onBeforeUnmount(() => store.releaseImages())
defineExpose({ reload: load })
</script>

<template>
  <section class="rounded-2xl border border-line bg-surface p-5" :aria-label="title" data-skin-photo-history>
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="mr-auto text-lg font-bold">{{ title }}</h2>
      <label v-if="sites.length > 1 && !compare" class="flex items-center gap-2 text-sm">
        部位
        <select v-model="site" class="min-h-10 rounded-lg border border-line bg-surface px-2" data-skin-site-filter>
          <option value="">全部</option>
          <option v-for="s in sites" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
      </label>
      <button v-if="comparable" type="button" class="min-h-10 rounded-full border px-4 text-sm"
        :class="compare ? 'border-care bg-care font-bold text-white' : 'border-line hover:bg-mist'" :aria-pressed="compare" data-skin-compare-toggle
        @click="toggleCompare">並排比較</button>
    </div>

    <p v-if="error" class="mt-2 text-critical" role="alert">{{ error.message }}</p>
    <div v-else-if="!data" class="mt-3 h-20 animate-pulse rounded-xl bg-mist" aria-busy="true" />
    <p v-else-if="!all.length" class="mt-2 text-ink-soft" data-skin-photo-empty>還沒有已送出的皮膚照片。</p>

    <!-- side by side: two time points of ONE body site -->
    <div v-else-if="compare" class="mt-3 space-y-3" data-skin-compare>
      <label class="flex flex-wrap items-center gap-2 text-sm">
        比較部位
        <select v-model="compareSite" class="min-h-10 rounded-lg border border-line bg-surface px-2" data-compare-site>
          <option v-for="s in sites" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
      </label>
      <p class="text-sm text-ink-soft" data-compare-note>
        只比較同一部位（{{ compareLabel }}）；不同部位不會被當成同一個病程。比較僅供照護參考，系統不會判斷改善或惡化。
      </p>
      <p v-if="points.length < 2" class="rounded-lg bg-mist px-3 py-2 text-sm" data-compare-single>這個部位只有一個時間點，還不能比較。</p>
      <div v-else class="grid grid-cols-2 gap-2">
        <div v-for="side in SIDES" :key="side.key" class="min-w-0 space-y-2" :data-compare-side="side.key">
          <select v-model="pick[side.key]" class="min-h-10 w-full rounded-lg border border-line bg-surface px-1 text-sm" :aria-label="side.label">
            <option v-for="(p, i) in points" :key="p.id" :value="p.id">{{ i + 1 }}. {{ short(p.at) }}</option>
          </select>
          <template v-if="pointById(pick[side.key])">
            <button v-for="ph in pointById(pick[side.key]).photos" :key="ph.id" type="button" class="block aspect-square w-full overflow-hidden rounded-lg border border-line"
              :aria-label="`放大 ${when(ph.created_at)} 的照片`" :data-compare-photo-site="siteKey(ph)" @click="viewing = ph">
              <SkinPhoto :photo-id="ph.id" variant="full" img-class="h-full w-full object-cover" />
            </button>
            <p class="text-xs text-ink-soft break-words">{{ pointById(pick[side.key]).record.answers }}</p>
          </template>
        </div>
      </div>
    </div>

    <!-- by date -->
    <div v-else class="mt-3 space-y-4">
      <div v-for="g in groups" :key="g.date" :data-skin-photo-date="g.date">
        <h3 class="text-sm font-bold text-ink-soft">{{ g.date }}</h3>
        <ul class="mt-1 grid grid-cols-3 gap-2 sm:grid-cols-4">
          <li v-for="p in g.photos" :key="p.id">
            <button type="button" class="block aspect-square w-full overflow-hidden rounded-lg border border-line" :aria-label="`放大 ${when(p.created_at)} 的照片`"
              :data-skin-photo-thumb="p.id" @click="viewing = p">
              <SkinPhoto :photo-id="p.id" />
            </button>
            <p class="mt-0.5 truncate text-xs text-ink-soft">{{ formatTime(p.created_at, TZ) }}・<span :class="p.body_site ? '' : 'font-bold text-warn'"
              :data-photo-site-label="siteKey(p)">{{ siteLabel(p) }}</span></p>
          </li>
        </ul>
      </div>
    </div>

    <SkinPhotoViewer v-if="viewing" :photo="viewing" :answers="viewing.record?.answers ?? ''" :notes="viewing.record?.notes ?? ''" @close="viewing = null" />
  </section>
</template>
