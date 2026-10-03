<script setup>
import { computed, onMounted, ref } from 'vue'

import { USE_MOCK } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import { useAuthStore } from '@/stores/auth'
import { usePatientPortalStore } from '@/stores/patientPortal'

/**
 * Patient: the link in the verification email (/patient/verify-email#token=…). The token is in the
 * URL fragment, so it never reaches a server log or a Referer header; it is removed from the
 * address bar as soon as it is read. Signed-out patients sign in first (the router keeps the link).
 */
const auth = useAuthStore()
const portal = usePatientPortalStore()
const patientId = computed(() => (USE_MOCK ? auth.user?.patient_id : 'me'))
const state = ref('working') // working | done | failed
const message = ref('')

onMounted(async () => {
  const token = new URLSearchParams(location.hash.slice(1)).get('token')
  history.replaceState(history.state, '', location.pathname) // forget the token
  if (!token) {
    state.value = 'failed'
    message.value = '驗證連結不完整，請重新開啟驗證信中的連結。'
    return
  }
  try {
    await portal.confirmVerification(patientId.value, token)
    state.value = 'done'
  } catch (e) {
    state.value = 'failed'
    message.value = e.message
  }
})
</script>

<template>
  <main class="mx-auto grid min-h-dvh max-w-xl place-items-center px-4 py-10" data-verify-email :data-state="state">
    <section class="w-full rounded-2xl border border-line bg-surface p-6 text-center">
      <template v-if="state === 'working'">
        <p class="text-lg" aria-busy="true">正在驗證 Email…</p>
      </template>
      <template v-else-if="state === 'done'">
        <AppIcon name="check" :size="40" class="mx-auto text-ok" />
        <h1 class="mt-2 text-xl font-bold">Email 驗證完成</h1>
        <p class="mt-2 text-ink-soft">現在可以在「我的」開啟「接收 Email 通知」。</p>
      </template>
      <template v-else>
        <h1 class="text-xl font-bold text-critical">無法完成驗證</h1>
        <p class="mt-2" role="alert" data-verify-failure>{{ message }}</p>
      </template>
      <RouterLink v-if="state !== 'working'" :to="{ name: 'patient-profile' }" class="mt-5 inline-flex min-h-12 items-center rounded-full bg-care px-6 font-bold text-white hover:bg-care/90" data-back-to-profile>
        回到「我的」
      </RouterLink>
    </section>
  </main>
</template>
