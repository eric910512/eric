<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import NurseNav from '@/components/NurseNav.vue'
import OneTimePassword from '@/components/OneTimePassword.vue'
import StaffNav from '@/components/StaffNav.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientsStore } from '@/stores/patients'

/**
 * 病人管理 (nurse) / 病人與照護團隊 (admin): the patients the signed-in user may see — a nurse
 * only currently assigned ones, an admin everyone — and creating a patient (the code is generated).
 */
const auth = useAuthStore()
const dashboard = useDashboardStore()
const store = usePatientsStore()

const isAdmin = computed(() => auth.role === 'admin')
const detailRoute = (id) => ({ name: isAdmin.value ? 'admin-patient' : 'nurse-patient', params: { id } })
const GENDER = { male: '男', female: '女', other: '其他' }
const summary = (p) => [
  p.primary_diagnosis ? `${p.primary_diagnosis.name_zh}${p.primary_diagnosis.stage ? ` 第 ${p.primary_diagnosis.stage} 期` : ''}` : '尚未登錄診斷',
  p.current_cycle ? `第 ${p.current_cycle.cycle_number} 次療程第 ${p.current_cycle.cycle_day} 天` : null,
].filter(Boolean).join('，')

const q = ref('')
const route = useRoute()
const assigned = ref(['true', 'false'].includes(route.query.assigned) ? route.query.assigned : 'any') // 管理總覽 links ?assigned=false
const items = computed(() => store.list?.items ?? [])
const listError = computed(() => store.errors.list)

function search() {
  store.fetchList({ q: q.value.trim(), assigned: assigned.value, sort: 'patient_code', page: 1 })
}

// ------------------------------------------------------------------ create
const creating = ref(false)
const blank = () => ({ display_name: '', gender: '', date_of_birth: '', height_cm: '', blood_type: '', allergies: '', withAccount: false, email: '' })
const form = reactive(blank())
const fieldErrors = ref({})
const formError = ref('')
const submitting = ref(false)
const created = ref(null) // { patient, account } — the temporary password lives only here, until closed

const FIELD_TEXT = {
  display_name: '請輸入姓名（100 字以內）',
  gender: '請選擇性別',
  date_of_birth: '請輸入過去的日期（120 年內）',
  height_cm: '身高需介於 30–250 公分',
  'account.email': '請輸入有效的 email',
}

async function submit() {
  formError.value = ''
  const errs = {}
  if (!form.display_name.trim()) errs.display_name = FIELD_TEXT.display_name
  if (!form.gender) errs.gender = FIELD_TEXT.gender
  if (!form.date_of_birth) errs.date_of_birth = FIELD_TEXT.date_of_birth
  if (form.withAccount && !form.email.trim()) errs['account.email'] = FIELD_TEXT['account.email']
  fieldErrors.value = errs
  if (Object.keys(errs).length) return
  const payload = {
    display_name: form.display_name.trim(),
    gender: form.gender,
    date_of_birth: form.date_of_birth,
    ...(form.height_cm !== '' ? { height_cm: Number(form.height_cm) } : {}),
    ...(form.blood_type ? { blood_type: form.blood_type } : {}),
    ...(form.allergies.trim() ? { allergies: form.allergies.trim() } : {}),
    ...(form.withAccount ? { account: { email: form.email.trim() } } : {}),
  }
  submitting.value = true
  try {
    const result = await store.createPatient(payload)
    created.value = { patient: result, account: result.account }
    Object.assign(form, blank())
    creating.value = false
    search()
  } catch (e) {
    const byField = {}
    for (const d of e.details ?? []) byField[d.field] = e.status === 409 ? '這個 email 已經有帳號' : FIELD_TEXT[d.field] ?? d.issue
    fieldErrors.value = byField
    if (!e.details?.length) formError.value = e.message
  } finally {
    submitting.value = false
  }
}

function closeCreated() {
  created.value = null
}

onMounted(() => {
  dashboard.fetchSettings()
  search()
})
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav v-if="isAdmin" active="admin-patients" />
    <NurseNav v-else active="nurse-patients" />

    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <header class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="text-2xl font-bold">{{ isAdmin ? '病人與照護團隊' : '病人管理' }}</h1>
          <p class="text-ink-soft">
            {{ isAdmin ? '所有病人；指派護理師後，護理師才看得到病人資料。' : '目前指派給您的病人。' }}
          </p>
        </div>
        <button
          v-if="!creating"
          type="button"
          class="inline-flex min-h-12 items-center gap-2 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90"
          data-new-patient
          @click="creating = true; created = null"
        >
          <AppIcon name="user" :size="20" /> 新增病人
        </button>
      </header>

      <!-- One-time result -->
      <section v-if="created" class="space-y-3 rounded-2xl border border-line bg-surface p-5" aria-live="polite" data-created-patient>
        <p class="text-lg">
          已建立 <strong>{{ created.patient.display_name }}</strong>，病人代碼
          <strong class="text-care" data-created-code>{{ created.patient.patient_code }}</strong>
        </p>
        <p v-if="!isAdmin" class="text-ink-soft">新病人需要由管理者指派護理師；指派給您之後，才會出現在您的個案名單。</p>
        <OneTimePassword
          v-if="created.account"
          :email="created.account.email"
          :password="created.account.temporary_password"
          :who="`${created.patient.display_name}（${created.patient.patient_code}）`"
          @done="created.account = null"
        />
        <div class="flex flex-wrap gap-2">
          <RouterLink v-if="isAdmin" :to="detailRoute(created.patient.id)" class="inline-flex min-h-11 items-center rounded-full border border-line px-4 font-bold text-care hover:bg-care-soft">
            {{ created.account ? '前往指派護理師' : '前往病人資料' }}
          </RouterLink>
          <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-mist" @click="closeCreated">關閉</button>
        </div>
      </section>

      <!-- Create form -->
      <form v-if="creating" class="rounded-2xl border border-line bg-surface p-5" novalidate data-create-patient @submit.prevent="submit">
        <h2 class="text-lg font-bold">新增病人</h2>
        <p class="text-sm text-ink-soft">病人代碼由系統自動產生。請勿輸入身分證字號等真實身分資料。</p>
        <p v-if="formError" class="mt-3 rounded-xl bg-critical-soft px-4 py-3 text-critical" role="alert">{{ formError }}</p>
        <div class="mt-4 grid gap-4 sm:grid-cols-2">
          <label class="block">
            <span class="font-medium">姓名 <span class="text-critical">*</span></span>
            <input v-model="form.display_name" name="display_name" maxlength="100" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.display_name" />
            <span v-if="fieldErrors.display_name" class="mt-1 block text-sm text-critical">{{ fieldErrors.display_name }}</span>
          </label>
          <fieldset>
            <legend class="font-medium">性別 <span class="text-critical">*</span></legend>
            <div class="mt-1 flex flex-wrap gap-2">
              <label v-for="(label, value) in GENDER" :key="value" class="inline-flex min-h-12 items-center gap-2 rounded-xl border border-line px-4" :class="form.gender === value ? 'border-care bg-care-soft' : ''">
                <input v-model="form.gender" type="radio" name="gender" :value="value" /> {{ label }}
              </label>
            </div>
            <span v-if="fieldErrors.gender" class="mt-1 block text-sm text-critical">{{ fieldErrors.gender }}</span>
          </fieldset>
          <label class="block">
            <span class="font-medium">出生日期 <span class="text-critical">*</span></span>
            <input v-model="form.date_of_birth" type="date" name="date_of_birth" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" :aria-invalid="!!fieldErrors.date_of_birth" />
            <span v-if="fieldErrors.date_of_birth" class="mt-1 block text-sm text-critical">{{ fieldErrors.date_of_birth }}</span>
          </label>
          <label class="block">
            <span class="font-medium">身高（公分）</span>
            <input v-model="form.height_cm" type="number" inputmode="decimal" min="30" max="250" step="0.1" name="height_cm" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
            <span v-if="fieldErrors.height_cm" class="mt-1 block text-sm text-critical">{{ fieldErrors.height_cm }}</span>
          </label>
          <label class="block">
            <span class="font-medium">血型</span>
            <select v-model="form.blood_type" name="blood_type" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4">
              <option value="">未填</option>
              <option v-for="b in ['A', 'B', 'AB', 'O']" :key="b" :value="b">{{ b }}</option>
            </select>
          </label>
          <label class="block">
            <span class="font-medium">過敏史</span>
            <input v-model="form.allergies" name="allergies" maxlength="500" class="mt-1 block min-h-12 w-full rounded-xl border border-line px-4" />
          </label>
        </div>
        <div class="mt-4 rounded-xl bg-mist p-4">
          <label class="inline-flex min-h-11 items-center gap-2 font-medium">
            <input v-model="form.withAccount" type="checkbox" name="with_account" class="size-5" /> 同時建立病人的登入帳號
          </label>
          <label v-if="form.withAccount" class="mt-2 block">
            <span class="font-medium">登入 email</span>
            <input v-model="form.email" type="email" name="account_email" inputmode="email" autocomplete="off" class="mt-1 block min-h-12 w-full rounded-xl border border-line bg-surface px-4" :aria-invalid="!!fieldErrors['account.email']" />
            <span v-if="fieldErrors['account.email']" class="mt-1 block text-sm text-critical">{{ fieldErrors['account.email'] }}</span>
            <span class="mt-1 block text-sm text-ink-soft">系統會產生初始密碼，只顯示一次。</span>
          </label>
        </div>
        <div class="mt-5 flex flex-wrap gap-2">
          <button type="submit" class="min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="submitting">
            {{ submitting ? '建立中…' : '建立病人' }}
          </button>
          <button type="button" class="min-h-12 rounded-full px-5 text-ink-soft hover:bg-mist" @click="creating = false">取消</button>
        </div>
      </form>

      <!-- List -->
      <section class="rounded-2xl border border-line bg-surface" aria-labelledby="patients-title">
        <form class="flex flex-wrap items-end gap-3 border-b border-line px-5 py-3" role="search" @submit.prevent="search">
          <h2 id="patients-title" class="mr-auto text-lg font-bold">
            病人名單 <span v-if="store.list" class="text-base font-normal text-ink-soft">{{ store.list.meta.total }} 位</span>
          </h2>
          <label class="flex items-center gap-2 text-sm text-ink-soft">
            搜尋
            <input v-model="q" name="q" placeholder="代碼或姓名" class="min-h-10 w-36 rounded-lg border border-line px-3 text-ink" />
          </label>
          <label v-if="isAdmin" class="flex items-center gap-2 text-sm text-ink-soft">
            指派
            <select v-model="assigned" name="assigned" class="min-h-10 rounded-lg border border-line bg-surface px-2 text-ink" @change="search">
              <option value="any">全部</option>
              <option value="false">尚未指派</option>
              <option value="true">已指派</option>
            </select>
          </label>
          <button type="submit" class="min-h-10 rounded-full border border-line px-4 font-bold text-care hover:bg-care-soft">查詢</button>
        </form>

        <p v-if="listError" class="p-5 text-critical" role="alert">{{ listError.message }}</p>
        <p v-else-if="store.list && !items.length" class="p-5 text-ink-soft">
          {{ q || assigned !== 'any' ? '沒有符合條件的病人。' : isAdmin ? '還沒有病人，請按「新增病人」。' : '目前沒有指派給您的病人。' }}
        </p>
        <ul v-else-if="store.list" class="divide-y divide-line" data-patient-list>
          <li v-for="p in items" :key="p.id" :data-patient-code="p.patient_code">
            <RouterLink :to="detailRoute(p.id)" class="grid gap-x-4 gap-y-1 px-5 py-3 hover:bg-mist sm:grid-cols-[6rem_1fr_auto] sm:items-center">
              <span class="font-bold text-care">{{ p.patient_code }}</span>
              <span class="min-w-0">
                <span class="font-bold">{{ p.display_name }}</span>
                <span class="text-ink-soft">　{{ GENDER[p.gender] ?? '' }} {{ p.age }} 歲</span>
                <span class="block text-sm text-ink-soft">{{ summary(p) }}</span>
              </span>
              <span class="flex flex-wrap gap-1.5 text-sm sm:justify-end">
                <span v-if="p.care_team.primary_nurse" class="rounded-full bg-care-soft px-2 py-0.5 text-care" data-primary-nurse>主責 {{ p.care_team.primary_nurse.display_name }}</span>
                <span v-else-if="p.care_team.nurses.length" class="rounded-full bg-care-soft px-2 py-0.5 text-care">{{ p.care_team.nurses.length }} 位護理師</span>
                <span v-else class="rounded-full bg-warn-soft px-2 py-0.5 font-bold text-warn" data-unassigned>尚未指派</span>
                <span v-if="!p.has_account" class="rounded-full bg-mist px-2 py-0.5 text-ink-soft">無登入帳號</span>
              </span>
            </RouterLink>
          </li>
        </ul>
        <div v-else class="space-y-3 p-5" aria-busy="true">
          <div v-for="n in 3" :key="n" class="h-12 animate-pulse rounded bg-mist" />
        </div>
      </section>
    </main>
  </div>
</template>
