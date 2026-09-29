<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import AppointmentsPanel from '@/components/AppointmentsPanel.vue'
import AssessmentsPanel from '@/components/AssessmentsPanel.vue'
import RecordsPanel from '@/components/RecordsPanel.vue'
import RemindersPanel from '@/components/RemindersPanel.vue'
import ChemoPanel from '@/components/ChemoPanel.vue'
import NurseNav from '@/components/NurseNav.vue'
import OneTimePassword from '@/components/OneTimePassword.vue'
import StaffNav from '@/components/StaffNav.vue'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { usePatientsStore } from '@/stores/patients'
import { formatTime, localDate } from '@/utils/format'

/**
 * One patient's profile, care alerts, diagnoses, care team and login account. Nurses edit
 * the clinical parts; admins manage the care team (assign / end). What the page may show is
 * decided by the backend: an unassigned nurse gets 404 here like everywhere else.
 */
const auth = useAuthStore()
const dashboard = useDashboardStore()
const store = usePatientsStore()
const route = useRoute()

const TZ = 'Asia/Taipei'
const GENDER = { male: '男', female: '女', other: '其他' }
const ALERT_TYPE = { allergy: '過敏', limb_restriction: '肢體限制', fall_risk: '跌倒風險', isolation: '隔離', other: '其他' }
const SEVERITY = { high: '高', medium: '中', low: '低' }
const SITE = { left_arm: '左手', right_arm: '右手', leg: '腳' }
const DX_STATUS = { active: '治療中', remission: '緩解', recurrence: '復發', resolved: '已痊癒' }

const id = computed(() => route.params.id)
const isAdmin = computed(() => auth.role === 'admin')
const isNurse = computed(() => auth.role === 'nurse')
const patient = computed(() => store.details[id.value])
const error = computed(() => store.errors[`patient:${id.value}`])
const assignments = computed(() => store.assignments[id.value] ?? [])
const teamLoaded = computed(() => id.value in store.assignments)
const when = (iso) => (iso ? `${localDate(iso, TZ)} ${formatTime(iso, TZ)}` : '—')

const notice = ref('')
const actionError = ref('')
function flash(text) {
  notice.value = text
  actionError.value = ''
}
async function act(fn, done) {
  actionError.value = ''
  notice.value = ''
  try {
    await fn()
    if (done) flash(done)
    return true
  } catch (e) {
    actionError.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message
    if (e.status === 404) await store.fetchPatient(id.value)
    return false
  }
}

// ------------------------------------------------------------------ profile
const editing = ref(false)
const profile = reactive({})
const profileErrors = ref({})
function startEdit() {
  const p = patient.value
  Object.assign(profile, {
    display_name: p.display_name, gender: p.gender, date_of_birth: p.date_of_birth, height_cm: p.height_cm ?? '',
    blood_type: p.blood_type ?? '', allergies: p.allergies ?? '', baseline_ecog: p.baseline_ecog ?? '',
  })
  profileErrors.value = {}
  editing.value = true
}
async function saveProfile() {
  const p = patient.value
  const next = {
    display_name: profile.display_name.trim(), gender: profile.gender, date_of_birth: profile.date_of_birth,
    height_cm: profile.height_cm === '' ? null : Number(profile.height_cm), blood_type: profile.blood_type || null,
    allergies: profile.allergies.trim() || null, baseline_ecog: profile.baseline_ecog === '' ? null : Number(profile.baseline_ecog),
  }
  const changes = Object.fromEntries(Object.entries(next).filter(([k, v]) => v !== p[k]))
  if (!Object.keys(changes).length) {
    editing.value = false
    return
  }
  profileErrors.value = {}
  try {
    await store.updatePatient(id.value, changes)
    editing.value = false
    flash('已儲存基本資料')
  } catch (e) {
    profileErrors.value = Object.fromEntries((e.details ?? []).map((d) => [d.field, d.issue]))
    actionError.value = e.status === 404 ? '找不到資料，或您已經沒有這位病人的權限。' : e.message
  }
}

// ------------------------------------------------------------------ care alerts / diagnoses (nurse)
const alertForm = reactive({ open: false, alert_type: 'allergy', body_site: '', description: '', severity: 'medium' })
async function addAlert() {
  const payload = { alert_type: alertForm.alert_type, description: alertForm.description, severity: alertForm.severity,
    ...(alertForm.alert_type === 'limb_restriction' ? { body_site: alertForm.body_site || null } : {}) }
  if (await act(() => store.addCareAlert(id.value, payload), '已新增照護注意事項')) {
    Object.assign(alertForm, { open: false, description: '', body_site: '' })
  }
}
const deactivateAlert = (a) => act(() => store.updateCareAlert(id.value, a.id, { is_active: false }), `已停用「${a.description}」`)

const dxForm = reactive({ open: false, cancer_type_code: '', stage: '', diagnosis_date: '', is_primary: true })
async function openDx() {
  dxForm.open = true
  const types = await store.fetchCancerTypes()
  if (!dxForm.cancer_type_code && types?.length) dxForm.cancer_type_code = types[0].code
}
async function addDx() {
  const payload = { cancer_type_code: dxForm.cancer_type_code, diagnosis_date: dxForm.diagnosis_date, is_primary: dxForm.is_primary,
    ...(dxForm.stage.trim() ? { stage: dxForm.stage.trim() } : {}) }
  if (await act(() => store.addDiagnosis(id.value, payload), '已新增診斷')) Object.assign(dxForm, { open: false, stage: '', diagnosis_date: '' })
}

// ------------------------------------------------------------------ care team (admin)
const assignForm = reactive({ nurse_id: '', is_primary: true })
const activeNurseIds = computed(() => new Set(assignments.value.filter((a) => a.active).map((a) => a.nurse.id)))
const assignable = computed(() => (store.staff ?? []).filter((n) => n.is_active && !activeNurseIds.value.has(n.id)))
async function assign() {
  const nurse = store.staff.find((n) => n.id === assignForm.nurse_id)
  if (!nurse) return
  if (await act(() => store.assignNurse(id.value, { nurse_id: nurse.id, is_primary: assignForm.is_primary }), `已指派 ${nurse.display_name}`)) {
    assignForm.nurse_id = ''
  }
}
const confirmEnd = ref(null) // assignment id awaiting confirmation
async function endAssignment(a) {
  if (await act(() => store.endAssignment(id.value, a.id), `已結束 ${a.nurse.display_name} 的指派，對方已無法查看這位病人`)) confirmEnd.value = null
}

// ------------------------------------------------------------------ login account
const accountEmail = ref('')
const issued = ref(null) // { email, temporary_password } — shown once, local only
async function createAccount() {
  let result = null
  if (await act(async () => { result = await store.createAccount(id.value, accountEmail.value.trim()) })) {
    issued.value = result
    accountEmail.value = ''
  }
}

async function load() {
  editing.value = false
  issued.value = null
  notice.value = ''
  actionError.value = ''
  const p = await store.fetchPatient(id.value)
  if (!p) return
  await Promise.all([store.fetchAssignments(id.value), ...(isAdmin.value ? [store.fetchStaff()] : [])])
}
watch(id, (v) => v && load(), { immediate: true })
dashboard.fetchSettings()
</script>

<template>
  <div class="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
    <StaffNav v-if="isAdmin" active="admin-patients" />
    <NurseNav v-else active="nurse-patients" />

    <main class="min-w-0 space-y-4 p-4 lg:p-6">
      <RouterLink :to="{ name: isAdmin ? 'admin-patients' : 'nurse-patients' }" class="inline-flex min-h-11 items-center gap-1 text-care hover:underline">
        <AppIcon name="chevron" :size="18" class="rotate-180" /> 回病人名單
      </RouterLink>

      <!-- 404 / 403: the backend decides; the page never shows cached data then -->
      <section v-if="error && !patient" class="rounded-2xl border border-line bg-surface p-6" role="alert" data-patient-error :data-status="error.status">
        <h1 class="text-xl font-bold">{{ error.status === 403 ? '您沒有權限查看這個頁面' : error.status === 404 ? '找不到這位病人' : '無法載入病人資料' }}</h1>
        <p class="mt-1 text-ink-soft">
          {{ error.status === 404 ? '病人不存在，或目前沒有指派給您。指派結束後就無法再查看該病人的資料。' : error.message }}
        </p>
      </section>

      <template v-else-if="patient">
        <header class="rounded-2xl border border-line bg-surface p-5">
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 class="text-2xl font-bold" data-patient-name>{{ patient.display_name }}</h1>
            <span class="text-lg font-bold text-care" data-patient-code>{{ patient.patient_code }}</span>
            <span class="text-ink-soft">{{ GENDER[patient.gender] }} {{ patient.age }} 歲</span>
          </div>
          <p class="mt-1 text-ink-soft">
            <template v-if="patient.current_cycle">第 {{ patient.current_cycle.cycle_number }} 次療程第 {{ patient.current_cycle.cycle_day }} 天<template v-if="patient.current_cycle.in_nadir">（骨髓抑制期）</template></template>
            <template v-else>目前沒有進行中的療程</template>
          </p>
          <RouterLink v-if="isNurse" :to="{ name: 'nurse', params: { patientId: patient.id } }" class="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full bg-care px-4 font-bold text-white hover:bg-care/90">
            <AppIcon name="chart" :size="18" /> 開啟照護總覽
          </RouterLink>
        </header>

        <p v-if="notice" class="rounded-xl bg-ok-soft px-4 py-3 font-medium text-ok" role="status" data-notice>{{ notice }}</p>
        <p v-if="actionError" class="rounded-xl bg-critical-soft px-4 py-3 font-medium text-critical" role="alert" data-action-error>{{ actionError }}</p>

        <div class="grid gap-4 xl:grid-cols-2">
          <!-- Profile -->
          <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="profile-title">
            <div class="flex items-center justify-between gap-2">
              <h2 id="profile-title" class="text-lg font-bold">基本資料</h2>
              <button v-if="!editing" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" data-edit-profile @click="startEdit">編輯</button>
            </div>
            <dl v-if="!editing" class="mt-3 grid grid-cols-[6rem_1fr] gap-y-2">
              <dt class="text-ink-soft">出生日期</dt><dd>{{ patient.date_of_birth }}</dd>
              <dt class="text-ink-soft">身高</dt><dd>{{ patient.height_cm != null ? `${patient.height_cm} 公分` : '—' }}</dd>
              <dt class="text-ink-soft">血型</dt><dd>{{ patient.blood_type ?? '—' }}</dd>
              <dt class="text-ink-soft">過敏史</dt><dd data-allergies>{{ patient.allergies ?? '—' }}</dd>
              <dt class="text-ink-soft">ECOG</dt><dd>{{ patient.baseline_ecog ?? '—' }}</dd>
              <dt class="text-ink-soft">建立</dt><dd class="text-sm">{{ when(patient.created_at) }}<template v-if="patient.created_by">，{{ patient.created_by.display_name }}</template></dd>
            </dl>
            <form v-else class="mt-3 grid gap-3" novalidate data-profile-form @submit.prevent="saveProfile">
              <label class="block"><span class="text-sm font-medium">姓名</span>
                <input v-model="profile.display_name" name="display_name" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
                <span v-if="profileErrors.display_name" class="text-sm text-critical">請輸入姓名</span>
              </label>
              <label class="block"><span class="text-sm font-medium">性別</span>
                <select v-model="profile.gender" name="gender" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option v-for="(label, value) in GENDER" :key="value" :value="value">{{ label }}</option>
                </select>
              </label>
              <label class="block"><span class="text-sm font-medium">出生日期</span>
                <input v-model="profile.date_of_birth" type="date" name="date_of_birth" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
                <span v-if="profileErrors.date_of_birth" class="text-sm text-critical">請輸入過去的日期</span>
              </label>
              <div class="grid grid-cols-2 gap-3">
                <label class="block"><span class="text-sm font-medium">身高（公分）</span>
                  <input v-model="profile.height_cm" type="number" step="0.1" name="height_cm" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
                  <span v-if="profileErrors.height_cm" class="text-sm text-critical">30–250</span>
                </label>
                <label class="block"><span class="text-sm font-medium">血型</span>
                  <select v-model="profile.blood_type" name="blood_type" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                    <option value="">未填</option>
                    <option v-for="b in ['A', 'B', 'AB', 'O']" :key="b" :value="b">{{ b }}</option>
                  </select>
                </label>
              </div>
              <label class="block"><span class="text-sm font-medium">過敏史</span>
                <input v-model="profile.allergies" name="allergies" maxlength="500" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
              </label>
              <label class="block"><span class="text-sm font-medium">ECOG（0–5）</span>
                <select v-model="profile.baseline_ecog" name="baseline_ecog" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option value="">未填</option>
                  <option v-for="n in 6" :key="n" :value="n - 1">{{ n - 1 }}</option>
                </select>
              </label>
              <div class="flex gap-2">
                <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">儲存</button>
                <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-mist" @click="editing = false">取消</button>
              </div>
            </form>
          </section>

          <!-- Care team -->
          <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="team-title" data-care-team>
            <h2 id="team-title" class="text-lg font-bold">照護團隊</h2>
            <div v-if="!teamLoaded" class="mt-3 h-12 animate-pulse rounded bg-mist" aria-busy="true" />
            <p v-else-if="!assignments.some((a) => a.active)" class="mt-2 font-bold text-warn" data-no-team>尚未指派護理師，護理師目前看不到這位病人。</p>
            <ul class="mt-3 divide-y divide-line">
              <li v-for="a in assignments" :key="a.id" class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2" :data-assignment="a.id" :data-active="a.active">
                <span class="min-w-0 flex-1">
                  <span class="font-bold" :class="a.active ? '' : 'text-ink-soft line-through'">{{ a.nurse.display_name }}</span>
                  <span v-if="a.is_primary" class="ml-2 rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">主責</span>
                  <span class="block text-sm text-ink-soft">
                    {{ when(a.assigned_at) }} 起<template v-if="a.ended_at">，{{ when(a.ended_at) }} 結束</template>
                  </span>
                </span>
                <template v-if="isAdmin && a.active">
                  <template v-if="confirmEnd === a.id">
                    <button type="button" class="min-h-10 rounded-full bg-critical px-4 font-bold text-white hover:bg-critical/90" data-confirm-end @click="endAssignment(a)">確定結束</button>
                    <button type="button" class="min-h-10 rounded-full px-3 text-ink-soft hover:bg-mist" @click="confirmEnd = null">取消</button>
                  </template>
                  <button v-else type="button" class="min-h-10 rounded-full border border-line px-4 text-critical hover:bg-critical-soft" data-end-assignment @click="confirmEnd = a.id">結束指派</button>
                </template>
              </li>
            </ul>
            <p v-if="confirmEnd" class="mt-2 text-sm text-critical">結束後，這位護理師會立刻無法查看這位病人的任何資料。</p>
            <form v-if="isAdmin && teamLoaded" class="mt-4 flex flex-wrap items-end gap-3 rounded-xl bg-mist p-3" data-assign-form @submit.prevent="assign">
              <label class="block min-w-48 flex-1">
                <span class="text-sm font-medium">指派護理師</span>
                <select v-model="assignForm.nurse_id" name="nurse_id" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option value="" disabled>{{ assignable.length ? '請選擇' : '沒有可指派的護理師' }}</option>
                  <option v-for="n in assignable" :key="n.id" :value="n.id">{{ n.display_name }}（負責 {{ n.active_patient_count }} 位）</option>
                </select>
              </label>
              <label class="inline-flex min-h-11 items-center gap-2"><input v-model="assignForm.is_primary" type="checkbox" name="is_primary" class="size-5" /> 主責</label>
              <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="!assignForm.nurse_id">指派</button>
            </form>
            <p v-else class="mt-3 text-sm text-ink-soft">照護團隊由管理者指派。</p>
          </section>

          <!-- Care alerts -->
          <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="alerts-title">
            <div class="flex items-center justify-between gap-2">
              <h2 id="alerts-title" class="text-lg font-bold">照護注意事項</h2>
              <button v-if="isNurse && !alertForm.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" @click="alertForm.open = true">新增</button>
            </div>
            <p v-if="!patient.care_alerts.length" class="mt-2 text-ink-soft">沒有注意事項。</p>
            <ul class="mt-2 space-y-2">
              <li v-for="a in patient.care_alerts" :key="a.id" class="flex flex-wrap items-center gap-2 rounded-xl px-3 py-2" :class="a.severity === 'high' ? 'bg-critical-soft' : 'bg-warn-soft'">
                <span class="font-bold" :class="a.severity === 'high' ? 'text-critical' : 'text-warn'">{{ ALERT_TYPE[a.alert_type] ?? a.alert_type }}</span>
                <span class="min-w-0 flex-1">{{ a.description }}<template v-if="a.body_site">（{{ SITE[a.body_site] ?? a.body_site }}）</template></span>
                <span class="text-sm text-ink-soft">嚴重度 {{ SEVERITY[a.severity] }}</span>
                <button v-if="isNurse" type="button" class="min-h-9 rounded-full px-3 text-sm text-ink-soft hover:bg-white" @click="deactivateAlert(a)">停用</button>
              </li>
            </ul>
            <form v-if="alertForm.open" class="mt-3 grid gap-3 rounded-xl bg-mist p-3 sm:grid-cols-2" @submit.prevent="addAlert">
              <label class="block"><span class="text-sm font-medium">類型</span>
                <select v-model="alertForm.alert_type" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option v-for="(label, value) in ALERT_TYPE" :key="value" :value="value">{{ label }}</option>
                </select>
              </label>
              <label class="block"><span class="text-sm font-medium">嚴重度</span>
                <select v-model="alertForm.severity" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option v-for="(label, value) in SEVERITY" :key="value" :value="value">{{ label }}</option>
                </select>
              </label>
              <label v-if="alertForm.alert_type === 'limb_restriction'" class="block"><span class="text-sm font-medium">部位</span>
                <select v-model="alertForm.body_site" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option value="" disabled>請選擇</option>
                  <option v-for="(label, value) in SITE" :key="value" :value="value">{{ label }}</option>
                </select>
              </label>
              <label class="block sm:col-span-2"><span class="text-sm font-medium">說明</span>
                <input v-model="alertForm.description" maxlength="255" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
              </label>
              <div class="flex gap-2 sm:col-span-2">
                <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">新增</button>
                <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="alertForm.open = false">取消</button>
              </div>
            </form>
          </section>

          <!-- Diagnoses -->
          <section class="rounded-2xl border border-line bg-surface p-5" aria-labelledby="dx-title">
            <div class="flex items-center justify-between gap-2">
              <h2 id="dx-title" class="text-lg font-bold">診斷</h2>
              <button v-if="isNurse && !dxForm.open" type="button" class="min-h-10 rounded-full border border-line px-4 text-care hover:bg-care-soft" @click="openDx">新增</button>
            </div>
            <p v-if="!patient.diagnoses.length" class="mt-2 text-ink-soft">尚未登錄診斷。</p>
            <ul class="mt-2 divide-y divide-line">
              <li v-for="d in patient.diagnoses" :key="d.id" class="py-2">
                <span class="font-bold">{{ d.cancer_type.name_zh }}</span>
                <span v-if="d.stage" class="ml-1">第 {{ d.stage }} 期</span>
                <span v-if="d.is_primary" class="ml-2 rounded-full bg-care-soft px-2 py-0.5 text-sm text-care">主要診斷</span>
                <span class="block text-sm text-ink-soft">{{ d.diagnosis_date }} 診斷，{{ DX_STATUS[d.status] ?? d.status }}</span>
              </li>
            </ul>
            <form v-if="dxForm.open" class="mt-3 grid gap-3 rounded-xl bg-mist p-3 sm:grid-cols-2" @submit.prevent="addDx">
              <label class="block"><span class="text-sm font-medium">癌別</span>
                <select v-model="dxForm.cancer_type_code" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3">
                  <option v-for="t in store.cancerTypes ?? []" :key="t.code" :value="t.code">{{ t.name_zh }}（{{ t.code }}）</option>
                </select>
              </label>
              <label class="block"><span class="text-sm font-medium">分期</span>
                <input v-model="dxForm.stage" maxlength="10" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
              </label>
              <label class="block"><span class="text-sm font-medium">診斷日期</span>
                <input v-model="dxForm.diagnosis_date" type="date" class="mt-1 block min-h-11 w-full rounded-xl border border-line bg-surface px-3" />
              </label>
              <label class="inline-flex min-h-11 items-center gap-2 self-end"><input v-model="dxForm.is_primary" type="checkbox" class="size-5" /> 主要診斷</label>
              <div class="flex gap-2 sm:col-span-2">
                <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90">新增</button>
                <button type="button" class="min-h-11 rounded-full px-4 text-ink-soft hover:bg-white" @click="dxForm.open = false">取消</button>
              </div>
            </form>
          </section>

          <ChemoPanel class="xl:col-span-2" :patient-id="patient.id" :can-write="isNurse" :diagnoses="patient.diagnoses" :timezone="patient.timezone" />
          <AppointmentsPanel class="xl:col-span-2" :patient-id="patient.id" :can-write="isNurse" :timezone="patient.timezone" />
          <AssessmentsPanel class="xl:col-span-2" :patient-id="patient.id" :can-write="isNurse" :timezone="patient.timezone" />
          <RecordsPanel v-if="isNurse" class="xl:col-span-2" :patient-id="patient.id" :can-write="isNurse" :timezone="patient.timezone" />
          <RemindersPanel class="xl:col-span-2" :patient-id="patient.id" :can-write="isNurse || isAdmin" :has-account="!!patient.account?.has_account" :timezone="patient.timezone" />

          <!-- Login account -->
          <section class="rounded-2xl border border-line bg-surface p-5 xl:col-span-2" aria-labelledby="account-title" data-account>
            <h2 id="account-title" class="text-lg font-bold">病人登入帳號</h2>
            <OneTimePassword
              v-if="issued"
              class="mt-3"
              :email="issued.email"
              :password="issued.temporary_password"
              :who="`${patient.display_name}（${patient.patient_code}）`"
              @done="issued = null"
            />
            <dl v-if="patient.account.has_account" class="mt-3 grid grid-cols-[6rem_1fr] gap-y-2">
              <dt class="text-ink-soft">帳號</dt><dd class="break-all">{{ patient.account.email }}</dd>
              <dt class="text-ink-soft">狀態</dt>
              <dd data-account-state>{{ !patient.account.is_active ? '已停用' : patient.account.must_change_password ? '尚未設定新密碼（仍是初始密碼）' : '使用中' }}</dd>
              <dt class="text-ink-soft">最後登入</dt><dd>{{ when(patient.account.last_login_at) }}</dd>
            </dl>
            <form v-else class="mt-3 flex flex-wrap items-end gap-3" data-account-form @submit.prevent="createAccount">
              <p class="w-full text-ink-soft">這位病人還沒有登入帳號。建立後系統會產生初始密碼，只顯示一次。</p>
              <label class="block min-w-56 flex-1"><span class="text-sm font-medium">登入 email</span>
                <input v-model="accountEmail" type="email" name="email" inputmode="email" autocomplete="off" class="mt-1 block min-h-11 w-full rounded-xl border border-line px-3" />
              </label>
              <button type="submit" class="min-h-11 rounded-full bg-care px-5 font-bold text-white hover:bg-care/90 disabled:opacity-60" :disabled="!accountEmail.trim()">建立帳號</button>
            </form>
          </section>
        </div>
      </template>

      <div v-else class="space-y-4" aria-busy="true" aria-label="載入中">
        <div v-for="n in 3" :key="n" class="h-32 animate-pulse rounded-2xl bg-surface" />
      </div>
    </main>
  </div>
</template>
