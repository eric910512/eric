<script setup>
import { onMounted, reactive, ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import OneTimePassword from '@/components/OneTimePassword.vue'
import StaffNav from '@/components/StaffNav.vue'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientsStore } from '@/stores/patients'

/** 護理師帳號 (admin): nurse accounts and creating one (the system generates the first password). */
const dashboard = useDashboardStore()
const store = usePatientsStore()

const creating = ref(false)
const blank = () => ({ display_name: '', email: '', staff_code: '', department: '', title: '' })
const form = reactive(blank())
const fieldErrors = ref({})
const formError = ref('')
const submitting = ref(false)
const issued = ref(null) // { display_name, email, temporary_password } — local only, shown once

const FIELD_TEXT = {
  display_name: '請輸入姓名',
  email: '請輸入有效的 email',
  'nurse_profile.staff_code': '員工編號已被使用',
}

async function submit() {
  formError.value = ''
  const errs = {}
  if (!form.display_name.trim()) errs.display_name = FIELD_TEXT.display_name
  if (!form.email.trim()) errs.email = FIELD_TEXT.email
  fieldErrors.value = errs
  if (Object.keys(errs).length) return
  const profile = Object.fromEntries(['staff_code', 'department', 'title'].filter((k) => form[k].trim()).map((k) => [k, form[k].trim()]))
  submitting.value = true
  try {
    const nurse = await store.createNurse({ role: 'nurse', display_name: form.display_name.trim(), email: form.email.trim(), nurse_profile: profile })
    issued.value = { display_name: nurse.display_name, email: nurse.email, temporary_password: nurse.temporary_password }
    Object.assign(form, blank())
    creating.value = false
  } catch (e) {
    const byField = {}
    for (const d of e.details ?? []) byField[d.field] = d.field === 'email' && e.status === 409 ? '這個 email 已經有帳號' : FIELD_TEXT[d.field] ?? d.issue
    fieldErrors.value = byField
    if (!e.details?.length) formError.value = e.message
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  dashboard.fetchSettings()
  store.fetchStaff()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav active="admin-nurses" />

    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="text-2xl font-bold">護理師帳號</h1>
          <p class="text-ink-soft">建立帳號後，到「病人與照護團隊」把病人指派給護理師。</p>
        </div>
        <button v-if="!creating" type="button" class="inline-flex min-h-12 items-center gap-2 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90" data-new-nurse @click="creating = true; issued = null">
          <AppIcon name="user" :size="20" /> 新增護理師
        </button>
      </header>

      <OneTimePassword
        v-if="issued"
        :email="issued.email"
        :password="issued.temporary_password"
        :who="issued.display_name"
        @done="issued = null"
      />

      <form v-if="creating" class="rounded-2xl border border-line bg-surface p-5" novalidate data-create-nurse @submit.prevent="submit">
        <h2 class="text-lg font-bold">新增護理師</h2>
        <p class="text-sm text-ink-soft">初始密碼由系統產生，只顯示一次；護理師第一次登入時需設定新密碼。</p>
        <p v-if="formError" class="mt-3 rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">{{ formError }}</p>
        <div class="mt-4 grid gap-4 sm:grid-cols-2">
          <label class="block"><span class="font-medium">姓名 <span class="text-critical">*</span></span>
            <input v-model="form.display_name" name="display_name" maxlength="100" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.display_name" />
            <span v-if="fieldErrors.display_name" class="mt-1 block text-sm text-critical">{{ fieldErrors.display_name }}</span>
          </label>
          <label class="block"><span class="font-medium">登入 email <span class="text-critical">*</span></span>
            <input v-model="form.email" type="email" name="email" inputmode="email" autocomplete="off" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.email" />
            <span v-if="fieldErrors.email" class="mt-1 block text-sm text-critical">{{ fieldErrors.email }}</span>
          </label>
          <label class="block"><span class="font-medium">員工編號</span>
            <input v-model="form.staff_code" name="staff_code" maxlength="30" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
            <span v-if="fieldErrors['nurse_profile.staff_code']" class="mt-1 block text-sm text-critical">{{ fieldErrors['nurse_profile.staff_code'] }}</span>
          </label>
          <label class="block"><span class="font-medium">單位</span>
            <input v-model="form.department" name="department" maxlength="100" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
          </label>
          <label class="block"><span class="font-medium">職稱</span>
            <input v-model="form.title" name="title" maxlength="50" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
          </label>
        </div>
        <div class="mt-5 flex flex-wrap gap-2">
          <button type="submit" class="min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="submitting">
            {{ submitting ? '建立中…' : '建立帳號' }}
          </button>
          <button type="button" class="min-h-12 rounded-full px-5 text-ink-soft hover:bg-mist" @click="creating = false">取消</button>
        </div>
      </form>

      <section class="rounded-2xl border border-line bg-surface" aria-labelledby="nurses-title">
        <h2 id="nurses-title" class="border-b border-line px-5 py-3 text-lg font-bold">
          護理師 <span v-if="store.staff" class="text-base font-normal text-ink-soft">{{ store.staff.length }} 位</span>
        </h2>
        <p v-if="store.errors.staff" class="p-5 text-critical" role="alert">{{ store.errors.staff.message }}</p>
        <ul v-else-if="store.staff" class="divide-y divide-line" data-nurse-list>
          <li v-for="n in store.staff" :key="n.id" class="grid gap-x-4 gap-y-1 px-5 py-3 sm:grid-cols-[1fr_auto] sm:items-center" :data-nurse-email="n.email">
            <span class="min-w-0">
              <span class="font-bold">{{ n.display_name }}</span>
              <span v-if="n.nurse_profile?.staff_code" class="ml-2 text-ink-soft">{{ n.nurse_profile.staff_code }}</span>
              <span class="block text-sm break-all text-ink-soft">{{ n.email }}<template v-if="n.nurse_profile?.department">，{{ n.nurse_profile.department }}</template></span>
            </span>
            <span class="flex flex-wrap gap-1.5 text-sm sm:justify-end">
              <span class="rounded-full bg-care-soft px-2 py-0.5 text-care">負責 {{ n.active_patient_count }} 位</span>
              <span v-if="n.must_change_password" class="rounded-full bg-warn-soft px-2 py-0.5 text-warn">尚未設定新密碼</span>
              <span v-if="!n.is_active" class="rounded-full bg-mist px-2 py-0.5 text-ink-soft">已停用</span>
            </span>
          </li>
        </ul>
        <div v-else class="space-y-3 p-5" aria-busy="true">
          <div v-for="k in 2" :key="k" class="h-12 animate-pulse rounded bg-mist" />
        </div>
      </section>
    </main>
  </div>
</template>
