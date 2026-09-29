<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import OneTimePassword from '@/components/OneTimePassword.vue'
import StaffNav from '@/components/StaffNav.vue'
import { useAdminStore } from '@/stores/admin'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { formatTime, localDate } from '@/utils/format'

/**
 * 帳號狀態 (admin): every account (patients, nurses, admins) — disable / enable, clear a login
 * lockout, and create another admin. A disabled account stops working at once (the backend
 * rejects its tokens); an admin cannot disable themselves or the last active admin.
 */
const dashboard = useDashboardStore()
const store = useAdminStore()
const auth = useAuthStore()
const route = useRoute()

const ROLE_TEXT = { patient: '病人', nurse: '護理師', admin: '管理者' }
const filters = reactive({
  role: 'all',
  status: ['active', 'inactive', 'locked'].includes(route.query.status) ? route.query.status : 'any',
  q: '',
})
const confirming = ref(null) // account id awaiting "確定停用"
const busy = ref(null)
const rowError = ref({}) // account id → message
const notice = ref('')

const search = () => store.fetchAccounts({ ...filters, q: filters.q.trim() })
const stamp = (iso) => (iso ? `${localDate(iso)} ${formatTime(iso)}` : '從未登入')

async function run(acct, fn, done) {
  busy.value = acct.id
  rowError.value = { ...rowError.value, [acct.id]: '' }
  try {
    notice.value = `${acct.display_name}：${done(await fn())}`
  } catch (e) {
    rowError.value = { ...rowError.value, [acct.id]: e.message }
  } finally {
    busy.value = null
  }
}
const resetting = ref(null) // account id awaiting "確定重設"
async function resetPassword(acct) {
  await run(acct, () => store.resetPassword(acct.id), (r) => {
    issued.value = { display_name: r.display_name, email: r.email, temporary_password: r.temporary_password }
    resetting.value = null
    return '已重設密碼，下次登入需設定新密碼'
  })
}

async function patch(acct, body, done) {
  busy.value = acct.id
  rowError.value = { ...rowError.value, [acct.id]: '' }
  try {
    const updated = await store.updateAccount(acct.id, body)
    notice.value = `${updated.display_name}：${done}`
    confirming.value = null
  } catch (e) {
    rowError.value = { ...rowError.value, [acct.id]: e.message }
  } finally {
    busy.value = null
  }
}

// create admin
const creating = ref(false)
const form = reactive({ display_name: '', email: '' })
const formErrors = ref({})
const issued = ref(null) // one-time password panel — local only
async function createAdmin() {
  const errs = {}
  if (!form.display_name.trim()) errs.display_name = '請輸入姓名'
  if (!form.email.trim()) errs.email = '請輸入有效的 email'
  formErrors.value = errs
  if (Object.keys(errs).length) return
  try {
    const a = await store.createAdmin({ display_name: form.display_name.trim(), email: form.email.trim() })
    issued.value = { display_name: a.display_name, email: a.email, temporary_password: a.temporary_password }
    Object.assign(form, { display_name: '', email: '' })
    creating.value = false
  } catch (e) {
    const byField = {}
    for (const d of e.details ?? []) byField[d.field] = e.status === 409 ? '這個 email 已經有帳號' : d.issue
    formErrors.value = Object.keys(byField).length ? byField : { email: e.message }
  }
}

onMounted(() => {
  dashboard.fetchSettings()
  search()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-accounts" />
    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="text-2xl font-bold">帳號狀態</h1>
          <p class="text-ink-soft">停用後該帳號立即無法使用；資料與紀錄都會保留，可隨時重新啟用。</p>
        </div>
        <button v-if="!creating" type="button" class="inline-flex min-h-12 items-center gap-2 rounded-full border border-care px-5 font-bold text-care hover:bg-care-soft" data-new-admin @click="creating = true; issued = null">
          <AppIcon name="shield" :size="20" /> 新增管理者
        </button>
      </header>

      <OneTimePassword v-if="issued" :email="issued.email" :password="issued.temporary_password" :who="issued.display_name" @done="issued = null" />

      <form v-if="creating" class="rounded-2xl border border-line bg-surface p-5" novalidate data-create-admin @submit.prevent="createAdmin">
        <h2 class="text-lg font-bold">新增管理者</h2>
        <p class="text-sm text-ink-soft">護理師帳號請到「護理師帳號」建立。初始密碼由系統產生，只顯示一次。</p>
        <div class="mt-4 grid gap-4 sm:grid-cols-2">
          <label class="block"><span class="font-medium">姓名 <span class="text-critical">*</span></span>
            <input v-model="form.display_name" name="display_name" maxlength="100" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
            <span v-if="formErrors.display_name" class="mt-1 block text-sm text-critical">{{ formErrors.display_name }}</span>
          </label>
          <label class="block"><span class="font-medium">登入 email <span class="text-critical">*</span></span>
            <input v-model="form.email" type="email" name="email" autocomplete="off" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
            <span v-if="formErrors.email" class="mt-1 block text-sm text-critical">{{ formErrors.email }}</span>
          </label>
        </div>
        <div class="mt-5 flex flex-wrap gap-2">
          <button type="submit" class="min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90">建立帳號</button>
          <button type="button" class="min-h-12 rounded-full px-5 text-ink-soft hover:bg-mist" @click="creating = false">取消</button>
        </div>
      </form>

      <form class="flex flex-wrap items-end gap-3 rounded-2xl border border-line bg-surface p-4" role="search" data-account-filters @submit.prevent="search">
        <label class="block"><span class="text-sm text-ink-soft">角色</span>
          <select v-model="filters.role" name="role" class="mt-1 block min-h-12 rounded-xl border border-line bg-surface px-3" @change="search">
            <option value="all">全部</option><option value="patient">病人</option><option value="nurse">護理師</option><option value="admin">管理者</option>
          </select>
        </label>
        <label class="block"><span class="text-sm text-ink-soft">狀態</span>
          <select v-model="filters.status" name="status" class="mt-1 block min-h-12 rounded-xl border border-line bg-surface px-3" @change="search">
            <option value="any">全部</option><option value="active">啟用中</option><option value="inactive">已停用</option><option value="locked">登入鎖定中</option>
          </select>
        </label>
        <label class="block min-w-0 flex-1"><span class="text-sm text-ink-soft">姓名或 email</span>
          <input v-model="filters.q" name="q" type="search" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
        </label>
        <button type="submit" class="min-h-12 rounded-full bg-ink px-5 font-bold text-white">搜尋</button>
      </form>

      <p v-if="notice" class="rounded-xl bg-care-soft px-4 py-3 text-care" role="status" data-account-notice>{{ notice }}</p>

      <section class="rounded-2xl border border-line bg-surface" aria-labelledby="accounts-title">
        <h2 id="accounts-title" class="border-b border-line px-5 py-3 text-lg font-bold">
          帳號 <span v-if="store.accounts" class="text-base font-normal text-ink-soft">{{ store.accounts.items.length }} 個</span>
        </h2>
        <p v-if="store.errors.accounts" class="p-5 text-critical" role="alert">{{ store.errors.accounts.message }}</p>
        <p v-else-if="store.accounts && !store.accounts.items.length" class="p-5 text-ink-soft">沒有符合條件的帳號。</p>
        <ul v-else-if="store.accounts" class="divide-y divide-line" data-account-list>
          <li v-for="a in store.accounts.items" :key="a.id" class="grid gap-3 px-5 py-4 sm:grid-cols-[1fr_auto] sm:items-center" :data-account="a.email">
            <div class="min-w-0">
              <p>
                <span class="font-bold">{{ a.display_name }}</span>
                <span class="ml-2 rounded-full bg-mist px-2 py-0.5 text-sm">{{ ROLE_TEXT[a.role] }}</span>
                <span v-if="a.patient" class="ml-1 text-sm text-ink-soft">{{ a.patient.patient_code }}</span>
              </p>
              <p class="text-sm break-all text-ink-soft">{{ a.email }}，最近登入 {{ stamp(a.last_login_at) }}</p>
              <p class="mt-1 flex flex-wrap gap-1.5 text-sm">
                <span v-if="!a.is_active" class="rounded-full bg-critical-soft px-2 py-0.5 font-bold text-critical" data-inactive>已停用</span>
                <span v-if="a.locked" class="rounded-full bg-warn-soft px-2 py-0.5 text-warn" data-locked>登入鎖定至 {{ formatTime(a.locked_until) }}</span>
                <span v-if="a.must_change_password" class="rounded-full bg-warn-soft px-2 py-0.5 text-warn">尚未設定新密碼</span>
                <span v-if="a.active_sessions" class="rounded-full bg-care-soft px-2 py-0.5 text-care" data-active-sessions>{{ a.active_sessions }} 個裝置登入中</span>
              </p>
              <p v-if="rowError[a.id]" class="mt-1 text-sm text-critical" role="alert" data-row-error>{{ rowError[a.id] }}</p>
            </div>
            <div v-if="a.id !== auth.user?.id" class="flex flex-wrap gap-2 sm:justify-end">
              <button v-if="a.active_sessions" type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" :disabled="busy === a.id" data-revoke-sessions
                @click="run(a, () => store.revokeSessions(a.id), (n) => `已強制登出 ${n} 個裝置`)">強制登出</button>
              <button v-if="resetting !== a.id" type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" data-reset-password @click="resetting = a.id">重設密碼</button>
              <template v-else>
                <button type="button" class="min-h-11 rounded-full bg-ink px-4 font-bold text-white" :disabled="busy === a.id" data-confirm-reset @click="resetPassword(a)">確定重設</button>
                <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-mist" @click="resetting = null">取消</button>
              </template>
              <button v-if="a.locked" type="button" class="min-h-11 rounded-full border border-line px-4 hover:bg-mist" :disabled="busy === a.id" data-unlock @click="patch(a, { unlock: true }, '已解除登入鎖定')">解除鎖定</button>
              <template v-if="a.is_active">
                <button v-if="confirming !== a.id" type="button" class="min-h-11 rounded-full border border-line px-4 text-critical hover:bg-critical-soft" data-disable @click="confirming = a.id">停用帳號</button>
                <template v-else>
                  <button type="button" class="min-h-11 rounded-full bg-critical px-4 font-bold text-white" :disabled="busy === a.id" data-confirm-disable @click="patch(a, { is_active: false }, '帳號已停用')">確定停用</button>
                  <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-mist" @click="confirming = null">取消</button>
                </template>
              </template>
              <button v-else type="button" class="min-h-11 rounded-full bg-care px-4 font-bold text-white" :disabled="busy === a.id" data-enable @click="patch(a, { is_active: true }, '帳號已重新啟用')">重新啟用</button>
            </div>
            <p v-else class="text-sm text-ink-soft sm:text-right">目前登入的帳號</p>
          </li>
        </ul>
        <div v-else class="space-y-3 p-5" aria-busy="true">
          <div v-for="k in 3" :key="k" class="h-14 animate-pulse rounded bg-mist" />
        </div>
      </section>
    </main>
  </div>
</template>
