import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import AccountSessions from '@/views/AccountSessions.vue'
import AdminAccounts from '@/views/AdminAccounts.vue'
import AdminAudit from '@/views/AdminAudit.vue'
import AdminHome from '@/views/AdminHome.vue'
import AdminNurses from '@/views/AdminNurses.vue'
import AdminRules from '@/views/AdminRules.vue'
import AdminSettings from '@/views/AdminSettings.vue'
import CareTimeline from '@/views/CareTimeline.vue'
import ChangePassword from '@/views/ChangePassword.vue'
import ComingSoon from '@/views/ComingSoon.vue'
import Login from '@/views/Login.vue'
import MyTreatment from '@/views/MyTreatment.vue'
import NotificationCenter from '@/views/NotificationCenter.vue'
import NurseDashboard from '@/views/NurseDashboard.vue'
import NursingAssessments from '@/views/NursingAssessments.vue'
import PatientDashboard from '@/views/PatientDashboard.vue'
import PatientDetail from '@/views/PatientDetail.vue'
import PatientList from '@/views/PatientList.vue'
import PatientNotifications from '@/views/PatientNotifications.vue'
import PatientProfile from '@/views/PatientProfile.vue'
import PatientSymptoms from '@/views/PatientSymptoms.vue'
import ReviewQueue from '@/views/ReviewQueue.vue'
import VitalSignsEntry from '@/views/VitalSignsEntry.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'root', redirect: () => useAuthStore().homeRoute },
    { path: '/login', name: 'login', component: Login, meta: { title: '登入' } },
    {
      path: '/change-password',
      name: 'change-password',
      component: ChangePassword,
      meta: { title: '設定新密碼', requiresAuth: true },
    },
    {
      path: '/account/sessions',
      name: 'account-sessions',
      component: AccountSessions,
      meta: { title: '登入裝置', requiresAuth: true },
    },
    {
      path: '/patient',
      name: 'patient',
      component: PatientDashboard,
      meta: { title: '我的照護', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/patient/timeline',
      name: 'patient-timeline',
      component: CareTimeline,
      meta: { title: '照護時間軸', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/patient/symptoms',
      name: 'patient-symptoms',
      component: PatientSymptoms,
      meta: { title: '症狀回報', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/patient/notifications',
      name: 'patient-notifications',
      component: PatientNotifications,
      meta: { title: '通知', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/patient/me',
      name: 'patient-profile',
      component: PatientProfile,
      meta: { title: '我的', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/patient/treatment',
      name: 'patient-treatment',
      component: MyTreatment,
      meta: { title: '我的療程', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/patient/vitals',
      name: 'patient-vitals',
      component: VitalSignsEntry,
      meta: { title: '記錄生命徵象', requiresAuth: true, roles: ['patient'] },
    },
    {
      path: '/nurse/notifications',
      name: 'nurse-notifications',
      component: NotificationCenter,
      meta: { title: '通知中心', requiresAuth: true, roles: ['nurse'] },
    },
    {
      path: '/nurse/reviews',
      name: 'nurse-reviews',
      component: ReviewQueue,
      meta: { title: '待審清單', requiresAuth: true, roles: ['nurse'] },
    },
    {
      path: '/nurse/assessments',
      name: 'nurse-assessments',
      component: NursingAssessments,
      meta: { title: '護理評估', requiresAuth: true, roles: ['nurse'] },
    },
    {
      path: '/nurse/patients',
      name: 'nurse-patients',
      component: PatientList,
      meta: { title: '病人管理', requiresAuth: true, roles: ['nurse'] },
    },
    {
      path: '/nurse/patients/:id',
      name: 'nurse-patient',
      component: PatientDetail,
      meta: { title: '病人資料', requiresAuth: true, roles: ['nurse'] },
    },
    {
      path: '/nurse/:patientId?',
      name: 'nurse',
      component: NurseDashboard,
      meta: { title: '護理照護總覽', requiresAuth: true, roles: ['nurse'] },
    },
    {
      path: '/admin',
      name: 'admin-home',
      component: AdminHome,
      meta: { title: '管理總覽', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/patients',
      name: 'admin-patients',
      component: PatientList,
      meta: { title: '病人與照護團隊', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/patients/:id',
      name: 'admin-patient',
      component: PatientDetail,
      meta: { title: '病人資料', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/nurses',
      name: 'admin-nurses',
      component: AdminNurses,
      meta: { title: '護理師帳號', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/accounts',
      name: 'admin-accounts',
      component: AdminAccounts,
      meta: { title: '帳號狀態', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/audit',
      name: 'admin-audit',
      component: AdminAudit,
      meta: { title: '稽核紀錄', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/rules',
      name: 'admin-rules',
      component: AdminRules,
      meta: { title: '風險規則與量表', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/settings',
      name: 'admin-settings',
      component: AdminSettings,
      meta: { title: '系統設定', requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/soon/:feature',
      name: 'coming-soon',
      component: ComingSoon,
      meta: { title: '開發中', requiresAuth: true },
    },
    { path: '/:pathMatch(.*)*', redirect: { name: 'root' } },
  ],
  // Hash targets stop below the sticky page header (router scrolling ignores CSS scroll-margin).
  scrollBehavior: (to) => (to.hash ? { el: to.hash, top: 80, behavior: 'smooth' } : { top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.name === 'login') {
    return auth.isAuthenticated ? auth.homeRoute : true
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    // Access token expired: renew it from the session (refresh cookie). Only when that fails —
    // session ended, revoked or its refresh token expired — ask to sign in again.
    if (auth.hasSession && (await auth.refresh())) return true
    const expired = !!auth.token
    if (expired) auth.logout({ server: false })
    return { name: 'login', query: { redirect: to.fullPath, ...(expired ? { reason: 'expired' } : {}) } }
  }
  // Signed in with a temporary password: nothing but 設定新密碼 until it is changed (the API
  // enforces the same with 403 PASSWORD_CHANGE_REQUIRED).
  if (auth.mustChangePassword && to.name !== 'change-password') return { name: 'change-password' }
  if (to.meta.roles && !to.meta.roles.includes(auth.role)) {
    return auth.homeRoute // signed in, but this page belongs to another role
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title}｜化療照護` : '化療照護'
})
