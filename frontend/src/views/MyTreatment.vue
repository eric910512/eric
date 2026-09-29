<script setup>
import { computed, onMounted } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import { useAppointmentsStore } from '@/stores/appointments'
import { useAuthStore } from '@/stores/auth'
import { useChemotherapyStore } from '@/stores/chemotherapy'
import { useDashboardStore } from '@/stores/dashboard'
import { formatDate, formatTime, localDate } from '@/utils/format'

/** Patient: 我的療程 (/patient/treatment) — plan, cycles and the administrations (final records only). */
const auth = useAuthStore()
const dashboard = useDashboardStore()
const store = useChemotherapyStore()
const appointments = useAppointmentsStore()
const upcoming = computed(() => (appointments.byPatient[patientId.value] ?? [])
  .filter((a) => ['scheduled', 'checked_in'].includes(a.status) && Date.parse(a.scheduled_at) >= Date.now() - 12 * 3600000))
const APPT = { chemo_infusion: '化療注射', lab_draw: '抽血', clinic_visit: '門診', imaging: '影像檢查', radiotherapy: '放射治療', education_session: '衛教', other: '行程' }
const TZ = 'Asia/Taipei'
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const plans = computed(() => store.plans[patientId.value])
const meds = computed(() => store.medications[patientId.value] ?? [])
const plan = computed(() => plans.value?.find((p) => ['planned', 'active'].includes(p.status)) ?? plans.value?.[0] ?? null)
const error = computed(() => store.errors[`plans:${patientId.value}`])
const unread = computed(() => dashboard.patients[patientId.value]?.widgets.notifications?.unread_count ?? 0)

const CYCLE = { scheduled: '預定', in_progress: '進行中', completed: '已完成', delayed: '延後', cancelled: '取消' }
const PLAN = { planned: '尚未開始', active: '進行中', completed: '已完成', discontinued: '已停止' }
const GIVEN = { given: '已給藥', held: '暫停給藥', partial: '部分給藥', refused: '未給藥' }

onMounted(() => {
  store.fetchPatient(patientId.value)
  appointments.fetch(patientId.value)
  if (!dashboard.patients[patientId.value]) dashboard.fetchPatientDashboard(patientId.value)
})
</script>

<template>
  <div class="min-h-dvh pb-28">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="{ name: 'patient' }" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 首頁
        </RouterLink>
        <h1 class="text-lg font-bold">我的療程</h1>
      </div>
    </header>

    <main class="mx-auto max-w-xl space-y-4 px-4 pt-4" data-my-treatment>
      <p v-if="error" class="rounded-2xl bg-critical-soft p-4 text-critical" role="alert">{{ error.message }}</p>
      <div v-else-if="!plans" class="h-40 animate-pulse rounded-2xl bg-surface" aria-busy="true" />
      <p v-else-if="!plan" class="rounded-2xl border border-line bg-surface p-5 text-ink-soft">目前沒有化療療程。</p>

      <template v-else>
        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="plan-title">
          <h2 id="plan-title" class="text-xl font-bold">{{ plan.plan_name }}</h2>
          <p class="text-ink-soft">
            {{ plan.diagnosis?.name_zh }}<template v-if="plan.regimen">，{{ plan.regimen.name }}</template>，{{ PLAN[plan.status] }}
          </p>
          <p v-if="plan.attending_physician_name" class="text-ink-soft">主治醫師 {{ plan.attending_physician_name }}</p>
          <p v-if="plan.progress.current_cycle_number" class="mt-3 text-2xl font-bold">
            第 {{ plan.progress.current_cycle_number }} 次療程，第 {{ plan.progress.current_cycle_day }} 天
          </p>
          <p class="mt-1">已完成 {{ plan.progress.completed_cycles }} / {{ plan.total_cycles }} 次</p>
        </section>

        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="cycles-title">
          <h2 id="cycles-title" class="text-lg font-bold">每次療程</h2>
          <ol class="mt-2 divide-y divide-line">
            <li v-for="c in plan.cycles" :key="c.id" class="flex flex-wrap items-baseline justify-between gap-2 py-3" :data-cycle-status="c.status">
              <span class="font-bold">第 {{ c.cycle_number }} 次</span>
              <span class="text-ink-soft">
                <template v-if="c.actual_start_date">{{ formatDate(c.actual_start_date) }} 開始</template>
                <template v-else>預定 {{ formatDate(c.scheduled_date) }}</template>
              </span>
              <span class="rounded-full px-2 py-0.5 text-sm" :class="c.status === 'in_progress' ? 'bg-care text-white' : c.status === 'completed' ? 'bg-ok-soft text-ok' : 'bg-mist text-ink-soft'">{{ CYCLE[c.status] }}</span>
              <p v-if="c.in_nadir" class="w-full rounded-xl bg-nadir-soft px-3 py-2 text-nadir">目前在骨髓抑制期，抵抗力較弱。體溫 38°C 以上請立即聯絡醫療團隊。</p>
            </li>
          </ol>
        </section>

        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="meds-title">
          <h2 id="meds-title" class="text-lg font-bold">給藥紀錄</h2>
          <p v-if="!meds.length" class="mt-2 text-ink-soft">還沒有給藥紀錄。</p>
          <ul class="mt-2 divide-y divide-line" data-my-medications>
            <li v-for="m in meds" :key="m.id" class="py-3">
              <p class="font-bold">{{ m.drug.generic_name }} {{ m.dose_value }} {{ m.dose_unit }}</p>
              <p class="text-ink-soft">{{ localDate(m.administered_at, TZ) }} {{ formatTime(m.administered_at, TZ) }}，第 {{ m.cycle_number }} 次療程第 {{ m.cycle_day }} 天，{{ GIVEN[m.administration_status] }}</p>
            </li>
          </ul>
        </section>
      </template>

      <section v-if="appointments.byPatient[patientId]" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="upcoming-title" data-my-upcoming>
        <h2 id="upcoming-title" class="text-lg font-bold">接下來的行程</h2>
        <p v-if="!upcoming.length" class="mt-2 text-ink-soft">目前沒有排定的行程。</p>
        <ul class="mt-2 divide-y divide-line">
          <li v-for="a in upcoming" :key="a.id" class="py-3">
            <p class="font-bold">{{ formatDate(localDate(a.scheduled_at, TZ)) }} {{ formatTime(a.scheduled_at, TZ) }}　{{ a.title }}</p>
            <p class="text-ink-soft">{{ APPT[a.appointment_type] }}<template v-if="a.location">・{{ a.location }}</template><template v-if="a.status === 'checked_in'">・已報到</template></p>
            <ul v-if="a.instructions.length" class="mt-1 flex flex-wrap gap-2">
              <li v-for="i in a.instructions" :key="i.id" class="rounded-lg bg-action px-2 py-0.5 font-bold text-action-ink">{{ i.text }}</li>
            </ul>
          </li>
        </ul>
      </section>
    </main>

    <PatientBottomNav active="home" :unread="unread" />
  </div>
</template>
