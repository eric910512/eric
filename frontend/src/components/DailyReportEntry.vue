<script setup>
import AppIcon from '@/components/AppIcon.vue'

/**
 * daily-report widget (top of the patient home): the entry to 每日症狀與自我照護回報.
 * The form itself is on 症狀回報 (/patient/symptoms#daily-report). `reported_today` comes from the
 * backend's once-per-day rule (the patient's local day), so the home page never decides on its own.
 */
defineProps({
  report: { type: Object, default: null },
})
</script>

<template>
  <section v-if="report?.form" aria-label="每日症狀與自我照護回報" data-daily-entry :data-reported="report.reported_today">
    <div
      v-if="report.reported_today"
      class="flex min-h-16 items-center gap-3 rounded-2xl border-2 border-ok bg-ok-soft px-5 py-3 text-ok"
      role="status"
    >
      <span class="grid size-10 shrink-0 place-items-center rounded-full bg-ok text-white"><AppIcon name="check" /></span>
      <div class="min-w-0">
        <p class="text-xl font-bold" data-daily-entry-done>今日已回報</p>
        <p class="text-sm font-medium">每日症狀與自我照護回報，明天記得再回報。</p>
      </div>
    </div>
    <RouterLink
      v-else
      :to="{ name: 'patient-symptoms', hash: '#daily-report' }"
      class="flex min-h-16 items-center gap-3 rounded-2xl bg-care px-5 py-3 text-white shadow-sm hover:bg-care/90"
      data-daily-entry-link
    >
      <span class="grid size-10 shrink-0 place-items-center rounded-full bg-white/20"><AppIcon name="clipboard" /></span>
      <span class="min-w-0 flex-1">
        <span class="block text-lg font-bold leading-snug">每日症狀與自我照護回報</span>
        <span class="block text-sm">今天還沒回報，點這裡開始填寫</span>
      </span>
      <AppIcon name="chevron" :size="24" class="shrink-0" />
    </RouterLink>
  </section>
</template>
