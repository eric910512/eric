<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import PatientBottomNav from '@/components/PatientBottomNav.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientPortalStore } from '@/stores/patientPortal'

/** Patient: 我的 (/patient/me) — my basic data, care alerts, hospital contacts, password, sign out. */
const auth = useAuthStore()
const dashboard = useDashboardStore()
const portal = usePatientPortalStore()
const router = useRouter()
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const p = computed(() => portal.profile)
const unread = computed(() => dashboard.patients[patientId.value]?.widgets.notifications?.unread_count ?? 0)
const contacts = computed(() => dashboard.settings?.contacts ?? [])
const GENDER = { male: '男', female: '女', other: '其他' }
const ALERT = { allergy: '過敏', limb_restriction: '肢體限制', fall_risk: '跌倒風險', isolation: '隔離', other: '其他' }

function logout() {
  auth.logout()
  router.replace({ name: 'login', query: { reason: 'logout' } })
}

onMounted(() => {
  dashboard.fetchSettings()
  portal.fetchProfile(patientId.value)
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
        <h1 class="text-lg font-bold">我的</h1>
      </div>
    </header>

    <main class="mx-auto max-w-xl space-y-4 px-4 pt-4" data-patient-profile>
      <p v-if="portal.errors.profile" class="rounded-2xl bg-critical-soft p-4 text-critical" role="alert">{{ portal.errors.profile.message }}</p>
      <div v-else-if="!p" class="h-40 animate-pulse rounded-2xl bg-surface" aria-busy="true" />
      <template v-else>
        <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="me-title">
          <h2 id="me-title" class="text-xl font-bold">{{ p.display_name }}</h2>
          <p class="text-ink-soft">病人代碼 {{ p.patient_code }}</p>
          <dl class="mt-3 grid grid-cols-[6rem_1fr] gap-y-2">
            <dt class="text-ink-soft">性別</dt><dd>{{ GENDER[p.gender] ?? '—' }}</dd>
            <dt class="text-ink-soft">出生日期</dt><dd>{{ p.date_of_birth }}（{{ p.age }} 歲）</dd>
            <dt class="text-ink-soft">血型</dt><dd>{{ p.blood_type ?? '—' }}</dd>
            <dt class="text-ink-soft">過敏史</dt><dd>{{ p.allergies ?? '—' }}</dd>
          </dl>
          <p class="mt-3 text-sm text-ink-soft">資料有誤時，請告訴護理師協助修改。</p>
        </section>

        <section v-if="p.care_alerts.length" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="alerts-title">
          <h2 id="alerts-title" class="text-lg font-bold">照護注意事項</h2>
          <ul class="mt-2 space-y-2">
            <li v-for="a in p.care_alerts" :key="a.id" class="rounded-xl bg-warn-soft px-3 py-2"><strong class="text-warn">{{ ALERT[a.alert_type] ?? '注意' }}</strong>　{{ a.description }}</li>
          </ul>
        </section>
      </template>

      <section v-if="contacts.length" class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="contacts-title">
        <h2 id="contacts-title" class="text-lg font-bold">聯絡醫院</h2>
        <ul class="mt-2 space-y-2">
          <li v-for="c in contacts" :key="c.key">
            <a :href="`tel:${c.phone}`" class="flex min-h-12 items-center gap-2 rounded-full border border-line px-4 text-care hover:bg-care-soft">
              <AppIcon name="phone" :size="20" /> {{ c.label }} {{ c.phone }}
            </a>
          </li>
        </ul>
      </section>

      <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="account-title">
        <h2 id="account-title" class="text-lg font-bold">帳號</h2>
        <p class="mt-1 break-all text-ink-soft">{{ auth.user?.email }}</p>
        <div class="mt-3 flex flex-wrap gap-2">
          <RouterLink :to="{ name: 'change-password' }" class="inline-flex min-h-12 items-center rounded-full border border-line px-5 font-bold text-care hover:bg-care-soft" data-change-password-link>修改密碼</RouterLink>
          <RouterLink :to="{ name: 'account-sessions' }" class="inline-flex min-h-12 items-center rounded-full border border-line px-5 font-bold text-care hover:bg-care-soft" data-profile-sessions>登入裝置</RouterLink>
          <button type="button" class="min-h-12 rounded-full bg-ink px-5 font-bold text-white hover:bg-ink/90" data-logout @click="logout">登出</button>
        </div>
      </section>
    </main>

    <PatientBottomNav active="profile" :unread="unread" />
  </div>
</template>
