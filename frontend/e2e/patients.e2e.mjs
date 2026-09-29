// Sprint 1 — patient & care-team management, end to end in mock mode and API mode:
// admin creates a patient (code generated, one-time password) → assigns a nurse → creates a
// nurse → the patient signs in, must set a new password, reports symptoms → the nurse sees the
// patient and the alert → admin ends the assignment → the old nurse gets 404 everywhere → the
// new nurse (first sign-in, new password) sees the patient. Plus privacy, 390 px and the
// mock / API response shapes. Each mode runs in one tab (mock state lives in that tab).
import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
const desk = { width: 1440, height: 1400, deviceScaleFactor: 1 }
const phone = { width: 390, height: 1200, deviceScaleFactor: 1 }
const TEMP = /^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$/
const MOCK_STATE_KEY = 'ccp.mock.care-team' // the mock "server" (mock mode only)

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email, password = 'Demo@1234') =>
  (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })).body?.data?.access_token

const text = (p) => p.evaluate(() => document.body.innerText)
const path = (p) => p.evaluate(() => location.pathname)
const waitPath = (p, re, timeout = 10000) =>
  p.waitForFunction((src) => new RegExp(src).test(location.pathname), { timeout }, re.source).then(() => true, () => false)
const waitText = (p, s, timeout = 10000) =>
  p.waitForFunction((s) => document.body.innerText.includes(s), { timeout }, s).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
/** Set an input / select value the way typing would (v-model listens to input / change). */
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const clickText = (p, label, sel = 'button, a, label') => p.evaluate((label, sel) => {
  const el = [...document.querySelectorAll(sel)].find((b) => b.innerText.trim() === label)
  if (!el) throw new Error(`no element "${label}"`)
  el.click()
}, label, sel)
const answer = (p, question, value) => p.evaluate((question, value) => {
  const fs = [...document.querySelectorAll('fieldset')].find((f) => f.querySelector('legend').innerText.includes(question))
  ;[...fs.querySelectorAll('label')].find((l) => l.innerText.trim() === String(value)).click()
}, question, value)
/** Value of the nurse option whose label contains `name` in the assign form (waits for the staff list). */
const nurseOption = async (p, name) => {
  await p.waitForFunction((name) => [...document.querySelectorAll('[data-assign-form] option')].some((o) => o.innerText.includes(name)), { timeout: 8000 }, name).catch(() => {})
  return p.$$eval('[data-assign-form] option', (os, name) => os.find((o) => o.innerText.includes(name))?.value ?? '', name)
}
/** In-app navigation (no reload), like following a link. */
const go = async (p, to) => {
  await p.evaluate((to) => { history.pushState({}, '', to); dispatchEvent(new PopStateEvent('popstate')) }, to)
  await idle(p)
}

async function signIn(p, email, password = 'Demo@1234') {
  if (!(await path(p)).startsWith('/login')) await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', password)
  await p.click('button[type=submit]')
  await idle(p)
}
async function signOut(p) {
  await clickText(p, '登出', 'button')
  await waitPath(p, /^\/login/)
}
const storageDump = (p) => p.evaluate((skip) => JSON.stringify([
  ...Object.entries(sessionStorage).filter(([k]) => k !== skip), ...Object.entries(localStorage),
]), MOCK_STATE_KEY)

/** Key structure of a JSON value: objects → sorted keys (recursively); null matches anything. */
function shape(v) {
  if (Array.isArray(v)) return v.length ? [shape(v[0])] : []
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, shape(v[k])]))
  return v === null ? null : typeof v
}
function shapeDiff(a, b, at = '') {
  if (a === null || b === null) return []
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return [`${at}: array vs ${typeof b}`]
    return a.length && b.length ? shapeDiff(a[0], b[0], `${at}[]`) : []
  }
  if (typeof a === 'object' && typeof b === 'object') {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    return [...keys].flatMap((k) => (!(k in a) ? [`${at}.${k}: API only`] : !(k in b) ? [`${at}.${k}: mock only`] : shapeDiff(a[k], b[k], `${at}.${k}`)))
  }
  return a === b ? [] : [`${at}: ${a} vs ${b}`]
}
const shapes = { MOCK: {}, API: {} }

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const tag = mode.toLowerCase()
  const PATIENT_EMAIL = `e2e.patient.${tag}@demo.local`
  const NURSE_EMAIL = `e2e.nurse.${tag}@demo.local`
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  // ---------------------------------------------------------------- admin: create patient + account
  await signIn(p, 'admin01@demo.local')
  check(`${mode} admin: signs in to 管理總覽`, await waitPath(p, /^\/admin$/) && await waitText(p, '管理總覽'))
  await go(p, '/admin/patients')
  check(`${mode} admin: 病人與照護團隊 lists patients`, await waitText(p, '病人名單'))
  await p.waitForSelector('[data-patient-list]')
  const codesBefore = await p.$$eval('[data-patient-code]', (els) => els.map((e) => e.dataset.patientCode))
  const expected = `P${String(Math.max(...codesBefore.map((c) => Number(c.slice(1)))) + 1).padStart(5, '0')}`
  check(`${mode} admin: sees every patient (incl. P00001)`, codesBefore.includes('P00001'), codesBefore.join(','))

  await p.click('[data-new-patient]')
  await p.waitForSelector('[data-create-patient]')
  await setValue(p, '[data-create-patient] input[name=national_id]', 'x').catch(() => {}) // no such field exists
  check(`${mode} admin: create form has no patient-code or national-ID input`,
    !(await p.$('[data-create-patient] [name=patient_code], [data-create-patient] [name*=national]')))
  await p.click('[data-create-patient] button[type=submit]')
  await sleep(200)
  check(`${mode} admin: empty form blocked with field errors`, (await text(p)).includes('請輸入姓名') && (await text(p)).includes('請選擇性別'))
  await setValue(p, '[data-create-patient] input[name=display_name]', `測試病人 E2E${mode}`)
  await p.click('[data-create-patient] input[name=gender][value=female]')
  await setValue(p, '[data-create-patient] input[name=date_of_birth]', '1975-06-01')
  await setValue(p, '[data-create-patient] input[name=allergies]', '海鮮')
  await p.click('[data-create-patient] input[name=with_account]')
  await setValue(p, '[data-create-patient] input[name=account_email]', PATIENT_EMAIL)
  await p.click('[data-create-patient] button[type=submit]')
  const created = await p.waitForSelector('[data-created-code]', { timeout: 10000 }).then(() => true, () => false)
  const code = created ? await p.$eval('[data-created-code]', (e) => e.innerText.trim()) : null
  check(`${mode} admin: patient created with the next generated code`, code === expected, `${code} (expected ${expected})`)
  const patientPw = await p.$eval('[data-otp-password]', (e) => e.innerText.trim()).catch(() => '')
  check(`${mode} admin: one-time initial password shown`, TEMP.test(patientPw) && (await p.$eval('[data-otp-email]', (e) => e.innerText)) === PATIENT_EMAIL, patientPw)
  check(`${mode} admin: password not kept in browser storage`, !(await storageDump(p)).includes(patientPw))
  await p.screenshot({ path: `${OUT}/pat-${mode}-created.png`, fullPage: true })
  await p.click('[data-otp-done]')
  await sleep(150)
  check(`${mode} admin: closing the panel removes the password from the page`, !(await text(p)).includes(patientPw))
  check(`${mode} admin: new patient listed as 尚未指派`, await p.waitForFunction((code) => {
    const li = document.querySelector(`[data-patient-code="${code}"]`)
    return li && li.querySelector('[data-unassigned]')
  }, { timeout: 8000 }, code).then(() => true, () => false))

  // ---------------------------------------------------------------- admin: assign nurse01
  await p.click(`[data-patient-code="${code}"] a`)
  await p.waitForSelector('[data-assign-form]')
  const patientId = (await path(p)).split('/').pop()
  check(`${mode} admin: detail shows account waiting for a new password`, (await p.$eval('[data-account-state]', (e) => e.innerText)).includes('尚未設定新密碼'))
  const nurse01 = await nurseOption(p, '測試護理師 林')
  await p.select('[data-assign-form] select[name=nurse_id]', nurse01)
  await p.click('[data-assign-form] button[type=submit]')
  check(`${mode} admin: nurse01 assigned as primary`, await p.waitForFunction(() =>
    [...document.querySelectorAll('[data-assignment][data-active=true]')].some((li) => li.innerText.includes('測試護理師 林') && li.innerText.includes('主責')),
  { timeout: 8000 }).then(() => true, () => false))

  // ---------------------------------------------------------------- admin: create a nurse
  await clickText(p, '護理師帳號', 'a')
  await p.waitForSelector('[data-nurse-list]')
  await p.click('[data-new-nurse]')
  await setValue(p, '[data-create-nurse] input[name=display_name]', `測試護理師 陳${mode}`)
  await setValue(p, '[data-create-nurse] input[name=email]', NURSE_EMAIL)
  await setValue(p, '[data-create-nurse] input[name=staff_code]', `N9${mode === 'MOCK' ? 1 : 2}`)
  await p.click('[data-create-nurse] button[type=submit]')
  await p.waitForSelector('[data-otp-password]', { timeout: 10000 }).catch(() => {})
  const nursePw = await p.$eval('[data-otp-password]', (e) => e.innerText.trim()).catch(() => '')
  check(`${mode} admin: nurse account created with a one-time password`, TEMP.test(nursePw)
    && await p.waitForFunction((e) => document.querySelector(`[data-nurse-email="${e}"]`)?.innerText.includes('尚未設定新密碼'), { timeout: 5000 }, NURSE_EMAIL).then(() => true, () => false))
  await p.click('[data-otp-done]')
  if (mode === 'API') {
    const at = await tokenFor('admin01@demo.local')
    shapes.API.list = shape((await api('/patients', at)).body)
    shapes.API.detail = shape((await api(`/patients/${patientId}`, at)).body)
    shapes.API.assignments = shape((await api(`/patients/${patientId}/nurse-assignments`, at)).body)
    shapes.API.staff = shape((await api('/admin/users?role=nurse', at)).body)
  } else {
    const m = await p.evaluate(async (id) => {
      const mock = await import('/src/mock/api.js')
      return { list: mock.mockListPatients(), detail: mock.mockGetPatient(id), assignments: mock.mockListAssignments(id), staff: mock.mockListStaff() }
    }, patientId)
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
  }
  await signOut(p)

  // ---------------------------------------------------------------- patient: first sign-in → new password → report
  await signIn(p, PATIENT_EMAIL, patientPw)
  check(`${mode} patient: temporary password → 設定新密碼 first`, await waitPath(p, /^\/change-password$/) && (await text(p)).includes('您正在使用初始密碼'))
  await p.goto(base + '/patient', { waitUntil: 'networkidle0' })
  check(`${mode} patient: other pages stay locked until the password is changed (reload)`, (await path(p)) === '/change-password')
  await setValue(p, 'input[name=current_password]', patientPw)
  await setValue(p, 'input[name=new_password]', 'abcdefgh')
  await setValue(p, 'input[name=confirm_password]', 'abcdefgh')
  await p.click('[data-change-password] button[type=submit]')
  await sleep(200)
  check(`${mode} patient: weak password rejected (letters + digits)`, (await text(p)).includes('需要同時包含英文字母和數字') && (await path(p)) === '/change-password')
  await setValue(p, 'input[name=new_password]', 'E2eNewPass1')
  await setValue(p, 'input[name=confirm_password]', 'E2eNewPass1')
  await p.click('[data-change-password] button[type=submit]')
  check(`${mode} patient: password set → own dashboard`, await waitPath(p, /^\/patient$/) && await waitText(p, `測試病人 E2E${mode}`))
  check(`${mode} patient: new patient's dashboard renders (no data yet)`, await waitText(p, '今天的症狀'))
  await clickText(p, '開始回報', 'button')
  await p.waitForFunction(() => document.querySelectorAll('fieldset').length >= 4, { timeout: 8000 })
  await answer(p, '疼痛', 9)
  await answer(p, '噁心', 2)
  await answer(p, '疲倦', 3)
  await answer(p, '發燒', '有')
  await clickText(p, '送出回報', 'button')
  check(`${mode} patient: symptom report submitted (existing flow)`, await waitText(p, '已送出，謝謝您'))
  await idle(p)
  await signOut(p)

  // ---------------------------------------------------------------- nurse01: sees the patient and the alert
  await signIn(p, 'nurse01@demo.local')
  await waitText(p, '我的個案')
  check(`${mode} nurse01: new patient in the caseload`, await waitText(p, `測試病人 E2E${mode}`))
  await clickText(p, '病人管理', 'a')
  await p.waitForSelector('[data-patient-list]')
  check(`${mode} nurse01: 病人管理 lists the newly assigned patient`, !!(await p.$(`[data-patient-code="${code}"]`)))
  await p.click(`[data-patient-code="${code}"] a`)
  check(`${mode} nurse01: patient detail opens`, await p.waitForSelector('[data-patient-name]', { timeout: 8000 }).then(() => true, () => false)
    && (await p.$eval('[data-allergies]', (e) => e.innerText)) === '海鮮')
  check(`${mode} nurse01: care team is read-only for nurses`, !(await p.$('[data-assign-form], [data-end-assignment]')))
  await clickText(p, '開啟照護總覽', 'a')
  await waitPath(p, new RegExp(`^/nurse/${patientId}$`))
  check(`${mode} nurse01: fever report raised the risk (existing Risk Engine)`, await waitText(p, '高風險') && await waitText(p, '發燒'))
  if (mode === 'API') {
    await go(p, '/nurse/notifications')
    check('API nurse01: the alert reached the Notification Center (existing workflow)', await waitText(p, `測試病人 E2E${mode}`))
  }
  await signOut(p)

  // ---------------------------------------------------------------- admin: end nurse01, assign the new nurse
  await signIn(p, 'admin01@demo.local')
  await go(p, `/admin/patients/${patientId}`)
  await p.waitForSelector('[data-end-assignment]')
  await p.click('[data-end-assignment]')
  await p.click('[data-confirm-end]')
  check(`${mode} admin: assignment ended`, await waitText(p, '已結束') && !(await p.$('[data-assignment][data-active=true]')))
  const nurse2 = await nurseOption(p, '測試護理師 陳')
  await p.select('[data-assign-form] select[name=nurse_id]', nurse2)
  await p.click('[data-assign-form] button[type=submit]')
  check(`${mode} admin: new nurse assigned`, await p.waitForFunction(() =>
    [...document.querySelectorAll('[data-assignment][data-active=true]')].some((li) => li.innerText.includes('測試護理師 陳')), { timeout: 8000 }).then(() => true, () => false))
  await signOut(p)

  // ---------------------------------------------------------------- nurse01: lost access (404 everywhere)
  await signIn(p, 'nurse01@demo.local')
  await waitText(p, '我的個案')
  await idle(p)
  check(`${mode} old nurse: patient gone from the caseload`, !(await text(p)).includes(`測試病人 E2E${mode}`))
  await p.goto(`${base}/nurse/patients/${patientId}`, { waitUntil: 'networkidle0' })
  check(`${mode} old nurse: detail URL with the patient id → 404 page, no data`, await p.waitForSelector('[data-patient-error][data-status="404"]', { timeout: 8000 }).then(() => true, () => false)
    && !(await text(p)).includes('海鮮'))
  await go(p, `/nurse/${patientId}`)
  check(`${mode} old nurse: dashboard URL with the patient id → not found`, await waitText(p, '找不到') && !(await text(p)).includes(`測試病人 E2E${mode}`))
  if (mode === 'API') {
    const t = await tokenFor('nurse01@demo.local')
    const urls = [`/patients/${patientId}`, `/dashboard/patient/${patientId}`, `/patients/${patientId}/timeline`, `/labs/results/${patientId}`,
      `/symptoms/records/${patientId}`, `/patients/${patientId}/nurse-assignments`, `/notifications?patient_id=${patientId}&status=all`]
    const codes = []
    for (const u of urls) {
      const r = await api(u, t)
      codes.push(u.startsWith('/notifications') ? (r.body?.data?.length === 0 ? 'empty' : r.status) : r.status)
    }
    check('API old nurse: every endpoint for the patient → 404 (notifications list: none)', codes.every((c) => c === 404 || c === 'empty'), codes.join(','))
  } else {
    const r = await p.evaluate(async (id) => {
      const mock = await import('/src/mock/api.js')
      const out = []
      for (const call of [() => mock.mockGetPatient(id), () => mock.mockPatientDashboard(id), () => mock.mockTimeline(id), () => mock.mockLabHistory(id), () => mock.mockListRecords(id)]) {
        try { call(); out.push(200) } catch (e) { out.push(`${e.status ?? '?'}:${e.code}`) }
      }
      return out
    }, patientId)
    check('MOCK old nurse: every mock handler for the patient → 404 NOT_FOUND (same as the API)', r.every((x) => x === '404:NOT_FOUND'), r.join(','))
  }
  await signOut(p)

  // ---------------------------------------------------------------- new nurse: first sign-in at 390 px, then sees the patient
  await p.setViewport(phone)
  await signIn(p, NURSE_EMAIL, nursePw)
  check(`${mode} new nurse: temporary password → 設定新密碼`, await waitPath(p, /^\/change-password$/))
  check(`${mode} 390px: 設定新密碼 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-change-password.png`, fullPage: true })
  await setValue(p, 'input[name=current_password]', nursePw)
  await setValue(p, 'input[name=new_password]', 'ChenNurse2026')
  await setValue(p, 'input[name=confirm_password]', 'ChenNurse2026')
  await p.click('[data-change-password] button[type=submit]')
  check(`${mode} new nurse: sees the patient at once`, await waitPath(p, /^\/nurse$/) && await waitText(p, `測試病人 E2E${mode}`))
  await go(p, `/nurse/patients/${patientId}`)
  check(`${mode} new nurse: patient detail opens`, await p.waitForSelector('[data-patient-name]', { timeout: 8000 }).then(() => true, () => false))
  check(`${mode} 390px: patient detail has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-patient-detail.png`, fullPage: true })
  await signOut(p)

  // ---------------------------------------------------------------- 390 px admin pages
  await signIn(p, 'admin01@demo.local')
  await waitPath(p, /^\/admin$/)
  await go(p, '/admin/patients')
  await p.waitForSelector('[data-patient-list]')
  check(`${mode} 390px: admin patient list has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-admin-patients.png`, fullPage: true })
  await go(p, `/admin/patients/${patientId}`)
  await p.waitForSelector('[data-assign-form]')
  await p.waitForSelector('[data-assignment][data-active=true]')
  check(`${mode} 390px: care-team management has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-admin-patient.png`, fullPage: true })
  await go(p, '/admin/nurses')
  await p.waitForSelector('[data-nurse-list]')
  check(`${mode} 390px: nurse accounts page has no horizontal overflow`, await noOverflow(p))
  await signOut(p)
  await p.setViewport(desk)

  // ---------------------------------------------------------------- patient privacy
  await signIn(p, 'patient01@demo.local')
  await waitPath(p, /^\/patient$/)
  await go(p, '/admin/patients')
  check(`${mode} patient01: admin pages redirect home`, (await path(p)) === '/patient')
  await go(p, `/nurse/patients/${patientId}`)
  check(`${mode} patient01: nurse pages redirect home`, (await path(p)) === '/patient')
  if (mode === 'API') {
    const t = await tokenFor('patient01@demo.local')
    const r = [await api(`/patients/${patientId}`, t), await api(`/dashboard/patient/${patientId}`, t), await api('/patients', t)]
    check('API patient01: another patient by id → 404; the patient list → 403', r[0].status === 404 && r[1].status === 404 && r[2].status === 403, r.map((x) => x.status).join(','))
    const me = (await api('/patients/me', t)).body.data
    check('API patient01: own profile has no care team / account internals', me.patient_code === 'P00001' && !('care_team' in me) && !('account' in me))
  } else {
    const r = await p.evaluate(async (id) => {
      const mock = await import('/src/mock/api.js')
      const res = (f) => { try { f(); return 200 } catch (e) { return e.status } }
      const me = mock.mockGetPatient('me').data
      return { other: res(() => mock.mockGetPatient(id)), list: res(() => mock.mockListPatients()), me: me.patient_code, internals: 'care_team' in me || 'account' in me }
    }, patientId)
    check('MOCK patient01: another patient by id → 404; the patient list → 403', r.other === 404 && r.list === 403, JSON.stringify(r))
    check('MOCK patient01: own profile has no care team / account internals', r.me === 'P00001' && !r.internals)
  }
  await ctx.close()
}

// ------------------------------------------------------------------ mock / API consistency
for (const k of ['list', 'detail', 'assignments', 'staff']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}

check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
