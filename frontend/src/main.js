import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { configureAuth } from '@/api/client'
import App from '@/App.vue'
import { router } from '@/router'
import { useAuthStore } from '@/stores/auth'
import '@/styles/main.css'

const app = createApp(App)
app.use(createPinia())

const auth = useAuthStore()
configureAuth({
  tokenGetter: () => auth.token,
  // 401 TOKEN_EXPIRED: new access token from the refresh-token cookie (mock: the mock session)
  refresh: () => auth.refresh(),
  // Any 401 from the API (expired or invalid token): sign out and return to the login page.
  onUnauthorized: (code) => {
    const current = router.currentRoute.value
    auth.logout({ server: false })
    if (current.name !== 'login') {
      router.replace({
        name: 'login',
        query: { reason: code === 'TOKEN_EXPIRED' ? 'expired' : 'unauthorized', redirect: current.fullPath },
      })
    }
  },
  // 403 PASSWORD_CHANGE_REQUIRED: still on the temporary password → 設定新密碼.
  onPasswordChangeRequired: () => {
    auth.requirePasswordChange()
    if (router.currentRoute.value.name !== 'change-password') router.replace({ name: 'change-password' })
  },
})

auth.scheduleRefresh() // after a reload: keep renewing the access token of this tab's session

app.use(router)
app.mount('#app')
