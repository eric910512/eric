<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import { useAuthStore } from '@/stores/auth'
import { useSessionsStore } from '@/stores/sessions'
import { formatTime, localDate } from '@/utils/format'

/**
 * 登入裝置 (every role): where this account is signed in. End a device you no longer use, or sign
 * out everywhere else; the ended sign-ins stop working immediately. Changing the password also
 * ends every other sign-in.
 */
const auth = useAuthStore()
const store = useSessionsStore()
const router = useRouter()
const notice = ref('')
const failure = ref('')
const HOME = { patient: 'patient-profile', nurse: 'nurse', admin: 'admin-home' }
const back = computed(() => ({ name: HOME[auth.role] ?? 'root' }))
const stamp = (iso) => `${localDate(iso)} ${formatTime(iso)}`

/** "Chrome（Windows）"-style label from the user agent; unknown → 瀏覽器. */
function device(ua) {
  if (!ua) return '瀏覽器'
  const browser = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : '瀏覽器'
  const os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows' : /Mac OS/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : ''
  return os ? `${browser}（${os}）` : browser
}

async function end(s) {
  notice.value = ''
  failure.value = ''
  try {
    await store.end(s.id)
    if (s.current) {
      auth.logout({ server: false })
      router.replace({ name: 'login', query: { reason: 'logout' } })
      return
    }
    notice.value = '已登出該裝置'
  } catch (e) {
    failure.value = e.message
  }
}
async function endOthers() {
  notice.value = ''
  failure.value = ''
  try {
    const n = await store.endOthers()
    notice.value = n ? `已登出其他 ${n} 個裝置` : '沒有其他登入中的裝置'
  } catch (e) {
    failure.value = e.message
  }
}

onMounted(() => store.fetch())
</script>

<template>
  <div class="min-h-dvh">
    <header class="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
      <div class="mx-auto flex max-w-xl items-center gap-2 px-2 py-2">
        <RouterLink :to="back" class="flex min-h-12 items-center gap-1 rounded-full px-3 text-care hover:bg-care-soft">
          <AppIcon name="chevron" :size="20" class="rotate-180" /> 返回
        </RouterLink>
        <h1 class="text-lg font-bold">登入裝置</h1>
      </div>
    </header>
    <main class="mx-auto max-w-xl space-y-4 px-4 py-4" :data-loaded="store.items ? '' : undefined" data-account-sessions>
      <p class="text-ink-soft">這個帳號目前在以下裝置登入。不認得的裝置請登出，並修改密碼。</p>
      <p v-if="notice" class="rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-sessions-notice>{{ notice }}</p>
      <p v-if="failure || store.error" class="rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">{{ failure || store.error.message }}</p>
      <ul v-if="store.items" class="divide-y divide-line rounded-2xl border border-line bg-surface" data-session-list>
        <li v-for="s in store.items" :key="s.id" class="flex flex-wrap items-center justify-between gap-3 px-5 py-4" :data-session="s.id" :data-current="s.current ? 'true' : 'false'">
          <div class="min-w-0">
            <p class="font-bold">{{ device(s.user_agent) }} <span v-if="s.current" class="ml-1 rounded-full bg-ok-soft px-2 py-0.5 text-sm font-normal text-ok">這個裝置</span></p>
            <p class="text-sm text-ink-soft">登入於 {{ stamp(s.created_at) }}<template v-if="s.ip_address">，{{ s.ip_address }}</template></p>
          </div>
          <button type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-end-session @click="end(s)">{{ s.current ? '登出' : '登出這個裝置' }}</button>
        </li>
      </ul>
      <div v-else-if="!store.error" class="h-24 animate-pulse rounded-2xl bg-mist" aria-busy="true" />
      <button v-if="store.items && store.items.length > 1" type="button" class="min-h-12 w-full rounded-full bg-ink px-5 font-bold text-white hover:bg-ink/90" data-end-others @click="endOthers">
        登出其他所有裝置
      </button>
    </main>
  </div>
</template>
