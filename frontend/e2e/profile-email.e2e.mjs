// Patient profile + email notification sprint, in mock mode and API mode:
// patient 我的 (390 px) → email + height + weight → 儲存 → BMI → 驗證 Email (link from the mock /
// capture outbox) → 接收 Email 通知 → nurse (patient page) sees the masked contact → 發送通知 →
// app notification + email (summary only) → patient sees the app notification → handling status
// unchanged → email failure keeps the notification → permissions → audit / timeline (API) →
// mock / API response structure.
// Production simulation (E2E_PROD, staging, EMAIL_PROVIDER unset = disabled): API mode checks that
// nothing can be emailed (verification unavailable, deliveries skipped / not_configured) while the
// app notification works; mock mode runs the full flow.
import fs from 'node:fs'
import path from 'node:path'

import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const PROD = !!process.env.E2E_PROD
const OUTBOX_DIR = process.env.E2E_EMAIL_OUTBOX ?? ''
const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
const desk = { width: 1280, height: 1000, deviceScaleFactor: 1 }
const phone = { width: 390, height: 1400, deviceScaleFactor: 1 }
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const MOCK_P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e'
const TITLE = '明日治療提醒'
const MESSAGE = '明日上午 09:00 有治療行程，請提前 15 分鐘報到。'

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p) => p.evaluate(() => document.body.innerText)
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitText = (p, s, timeout = 10000) => p.waitForFunction((s) => document.body.innerText.includes(s), { timeout }, s).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 10000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const click = (p, sel) => p.$eval(sel, (b) => b.click())
const go = async (p, to) => {
  await p.evaluate((to) => { history.pushState({}, '', to); dispatchEvent(new PopStateEvent('popstate')) }, to)
  await idle(p)
}
async function signIn(p, email) {
  await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', 'Demo@1234')
  await p.click('button[type=submit]')
  await idle(p)
}
const signOut = async (p) => {
  await p.evaluate(() => [...document.querySelectorAll('button, a')].find((b) => b.innerText.trim() === '登出')?.click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
}
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

/** Emails recorded by the mock (sessionStorage of this tab) or the backend capture provider (JSON files). */
async function outbox(mode, p) {
  if (mode === 'MOCK') return p.evaluate(() => JSON.parse(sessionStorage.getItem('ccp.mock.email-outbox') ?? '[]'))
  if (!OUTBOX_DIR || !fs.existsSync(OUTBOX_DIR)) return []
  return fs.readdirSync(OUTBOX_DIR).sort().map((f) => JSON.parse(fs.readFileSync(path.join(OUTBOX_DIR, f), 'utf8')))
}
const linkOf = (m) => m.text.match(/(\/patient\/verify-email#token=\S+)/)?.[1]

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const emailing = mode === 'MOCK' || !PROD // the production simulation has no email provider
  const ADDRESS = `patient.e2e.${mode.toLowerCase()}@example.test`
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  let pid = MOCK_P1
  let otherPid = MOCK_P2
  let nt, pt
  if (mode === 'API') {
    nt = await tokenFor('nurse01@demo.local')
    pt = await tokenFor('patient01@demo.local')
    const at = await tokenFor('admin01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
    otherPid = (await api('/patients', at, { method: 'POST', body: JSON.stringify({ display_name: '測試病人 其他', gender: 'female', date_of_birth: '1970-01-01' }) })).body.data.id
  }
  /** Run a mock handler in the page (as the signed-in mock user) — the mock-mode equivalent of an API call. */
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message } } }
    }
  }, { name, args })

  // ---------------------------------------------------------------- patient: 我的 → 基本資料
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/me')
  check(`${mode} patient: 我的 has 基本資料 (Email, 身高, 體重, BMI, Email 通知)`, await waitSel(p, '[data-basic-profile] [data-save-basic]')
    && await waitSel(p, '[data-basic-profile] input[name=email]') && await waitSel(p, '[data-bmi]') && await waitSel(p, '[data-email-notify]'))
  check(`${mode} patient: Email 通知 cannot be switched on before verification`, await p.$eval('[data-email-notify]', (c) => c.disabled))
  await setValue(p, '[data-basic-profile] input[name=email]', ADDRESS)
  await setValue(p, '[data-basic-profile] input[name=height_cm]', '178')
  await setValue(p, '[data-basic-profile] input[name=weight_kg]', '75')
  await click(p, '[data-save-basic]')
  check(`${mode} patient: 儲存 → saved (basic data + weight)`, await waitText(p, '已儲存基本資料與體重'))
  check(`${mode} patient: BMI 75 kg / 1.78 m² = 23.7`, await waitFn(p, () => document.querySelector('[data-bmi]')?.innerText.trim() === '23.7'),
    await p.$eval('[data-bmi]', (e) => e.innerText))
  check(`${mode} patient: height and latest weight shown`, (await p.$eval('[data-height]', (e) => e.innerText)).includes('178')
    && (await p.$eval('[data-latest-weight]', (e) => e.innerText)).includes('75 kg'))
  check(`${mode} patient: email saved, 尚未驗證`, (await p.$eval('[data-email-state]', (e) => e.innerText)) === '尚未驗證')
  check(`${mode} patient: 體重紀錄 lists the new weight as 本人輸入 (history kept)`, await waitFn(p, () => {
    const rows = [...document.querySelectorAll('[data-weight-history] [data-weight]')]
    return rows.length >= 2 && rows[0].innerText.includes('75 kg') && rows[0].innerText.includes('本人輸入')
  }))
  check(`${mode} 390px: 我的 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-profile-basic.png`, fullPage: true })

  // ---------------------------------------------------------------- verification
  if (emailing) {
    await click(p, '[data-verify-email]')
    check(`${mode} patient: 驗證 Email → verification email sent`, await waitText(p, '驗證信已寄到'))
    const mail = (await outbox(mode, p)).filter((m) => m.purpose === 'verification' && m.to === ADDRESS).at(-1)
    const link = mail && linkOf(mail)
    check(`${mode} outbox: verification email to the contact address with a one-time link`, !!link, mail?.to)
    check(`${mode} verification email has no patient code / login email`, mail && !mail.text.includes('P00001') && !mail.text.includes('patient01@demo.local'))
    if (mode === 'API') {
      await p.goto(base + link, { waitUntil: 'networkidle0' }) // opening the link: a fresh page load (session renewed from the refresh cookie)
    } else {
      await go(p, link)
    }
    check(`${mode} patient: link → Email 驗證完成`, await waitSel(p, '[data-verify-email][data-state=done]'))
    check(`${mode} the token is removed from the address bar`, await p.evaluate(() => !location.hash.includes('token')))
    check(`${mode} 390px: verify page has no horizontal overflow`, await noOverflow(p))
    await go(p, '/patient/me')
    await go(p, link)
    check(`${mode} patient: the same link again → 已經使用過了`, await waitSel(p, '[data-verify-email][data-state=failed]') && await waitText(p, '已經使用過了'))
    await go(p, '/patient/me')
    check(`${mode} patient: Email 已驗證`, await waitFn(p, () => document.querySelector('[data-email-state]')?.innerText === '已驗證'))
    check(`${mode} patient: Email 通知 can now be switched on`, await waitFn(p, () => document.querySelector('[data-email-notify]')?.disabled === false))
    await click(p, '[data-email-notify]')
    await click(p, '[data-save-basic]')
    check(`${mode} patient: 接收 Email 通知 saved`, await waitText(p, '已儲存基本資料') && await p.$eval('[data-email-notify]', (c) => c.checked))
  } else {
    check(`${mode} (production, no provider): 驗證 Email unavailable, explained`, await waitSel(p, '[data-email-unavailable]') && !(await p.$('[data-verify-email]')))
    const r = await api('/patients/me/email-verification', pt, { method: 'POST' })
    check(`${mode} (production): verification request → 422 EMAIL_NOT_CONFIGURED`, r.status === 422 && r.body.error.code === 'EMAIL_NOT_CONFIGURED', r.status)
  }

  // shapes (patient)
  const profileRes = mode === 'API' ? await api('/patients/me/profile', pt) : await mockCall('mockGetProfile', [pid])
  const weightsRes = mode === 'API' ? await api(`/patients/${pid}/weights`, pt) : await mockCall('mockPatientWeights', [pid, {}])
  shapes[mode].profile = shape(profileRes.body)
  shapes[mode].weights = shape(weightsRes.body)
  check(`${mode} patient profile: own full email, verified ${emailing}, enabled ${emailing}`, profileRes.body.data.email === ADDRESS
    && profileRes.body.data.email_verified === emailing && profileRes.body.data.email_notification_enabled === emailing && profileRes.body.data.bmi === 23.7, JSON.stringify(profileRes.body.data))
  check(`${mode} latest weight is marked patient-entered (source patient_app)`, profileRes.body.data.latest_weight?.source === 'patient_app'
    && profileRes.body.data.latest_weight.entered_by_patient === true)

  // permissions (patient)
  const other = mode === 'API' ? await api(`/patients/${otherPid}/profile`, pt) : await mockCall('mockGetProfile', [otherPid])
  check(`${mode} patient cannot read another patient's profile → 404`, other.status === 404, other.status)
  const otherPatch = mode === 'API'
    ? await api(`/patients/${otherPid}/profile`, pt, { method: 'PATCH', body: JSON.stringify({ email: 'x@example.test' }) })
    : await mockCall('mockUpdateProfile', [otherPid, { email: 'x@example.test' }])
  check(`${mode} patient cannot change another patient's email → 404`, otherPatch.status === 404, otherPatch.status)
  await signOut(p)

  // ---------------------------------------------------------------- nurse: patient page → 發送通知
  await p.setViewport(desk)
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  check(`${mode} nurse: patient page shows the contact email masked only, with its state`, await waitSel(p, '[data-notification-contact]')
    && (await p.$eval('[data-contact-email]', (e) => e.innerText)) === `p***@example.test`
    && (await p.$eval('[data-contact-verified]', (e) => e.innerText)) === (emailing ? '已驗證' : '未驗證')
    && (await p.$eval('[data-contact-enabled]', (e) => e.innerText)) === (emailing ? '開啟' : '關閉')
    && !(await text(p)).includes(ADDRESS))
  check(`${mode} nurse: 發送通知 explains the email channel`, await waitSel(p, '[data-email-channel]')
    && (await p.$eval('[data-email-channel]', (e) => e.innerText)).includes(emailing ? '已開啟 Email 通知' : '尚未驗證'))
  const nurseView = mode === 'API' ? await api(`/patients/${pid}/profile`, nt) : await mockCall('mockGetProfile', [pid])
  shapes[mode].staffProfile = shape(nurseView.body)
  check(`${mode} nurse profile API: email null, masked only`, nurseView.body.data.email === null && nurseView.body.data.email_masked === 'p***@example.test')
  const nursePatch = mode === 'API'
    ? await api(`/patients/${pid}/profile`, nt, { method: 'PATCH', body: JSON.stringify({ email: 'nurse@example.test' }) })
    : await mockCall('mockUpdateProfile', [pid, { email: 'nurse@example.test' }])
  check(`${mode} nurse cannot change the patient's email → 403`, nursePatch.status === 403, nursePatch.status)

  const before = (await outbox(mode, p)).filter((m) => m.purpose === 'notification').length
  await click(p, '[data-new-reminder]')
  await setValue(p, '[data-reminder-form] input[name=title]', `${TITLE} ${mode}`)
  await setValue(p, '[data-reminder-form] textarea[name=message]', MESSAGE)
  await click(p, '[data-reminder-form] button[type=submit]')
  const expected = emailing ? 'Email 已寄出' : '系統尚未設定 Email 服務'
  check(`${mode} nurse: 發送通知 → app notification sent; email: ${expected}`, await waitText(p, '已送出，病人可在「通知」看到') && await waitText(p, expected))
  check(`${mode} nurse: the notification keeps its handling status (待處理), email status shown separately`, await waitFn(p, (a) =>
    [...document.querySelectorAll('[data-sent-list] [data-reminder]')].some((li) => li.innerText.includes(a.t) && li.dataset.status === 'new'
      && li.querySelector('[data-email-delivery]')?.dataset.emailStatus === a.s), { t: `${TITLE} ${mode}`, s: emailing ? 'sent' : 'skipped' }))
  const mails = (await outbox(mode, p)).filter((m) => m.purpose === 'notification')
  if (emailing) {
    const mail = mails.at(-1)
    check(`${mode} outbox: one notification email to the patient's verified address`, mails.length === before + 1 && mail.to === ADDRESS && mail.status === 'sent', mail?.to)
    check(`${mode} notification email = summary (no title / content / patient code)`, mail.text.includes('您有一則來自護理團隊的新通知')
      && mail.text.includes('發送時間') && mail.text.includes('安全提醒') && !mail.text.includes(MESSAGE) && !mail.text.includes(TITLE) && !mail.text.includes('P00001'))
  } else {
    check(`${mode} (production): nothing emailed`, mails.length === before)
  }
  const created = mode === 'API'
    ? (await api(`/notifications?type=reminder&patient_id=${pid}&per_page=50`, nt)).body.data.find((n) => n.title === `${TITLE} ${mode}`)
    : (await mockCall('mockListReminders', [pid, {}])).body.data.find((n) => n.title === `${TITLE} ${mode}`)
  shapes[mode].reminder = shape(created)
  check(`${mode} staff payload: status new + email_delivery ${emailing ? 'sent' : 'skipped / not_configured'}`, created?.status === 'new'
    && created.email_delivery.status === (emailing ? 'sent' : 'skipped') && (emailing || created.email_delivery.skip_reason === 'not_configured'), JSON.stringify(created?.email_delivery))

  // email failure (mock / capture test hook): the notification still goes out
  if (emailing) {
    const FAIL = 'patient.e2e+fail@example.test'
    await signOut(p)
    await signIn(p, 'patient01@demo.local')
    const call = (name, args, method, url, body) => (mode === 'API'
      ? api(url, pt, { method, body: body ? JSON.stringify(body) : undefined })
      : mockCall(name, args))
    await call('mockUpdateProfile', [pid, { email: FAIL }], 'PATCH', '/patients/me/profile', { email: FAIL })
    const sent = await call('mockRequestEmailVerification', [pid], 'POST', '/patients/me/email-verification')
    const failLink = linkOf((await outbox(mode, p)).filter((m) => m.purpose === 'verification' && m.to === FAIL).at(-1) ?? { text: '' })
    const token = failLink?.split('#token=')[1]
    const confirmed = await call('mockConfirmEmailVerification', [pid, { token }], 'POST', '/patients/me/email-verification/confirm', { token })
    const enabled = await call('mockUpdateProfile', [pid, { email_notification_enabled: true }], 'PATCH', '/patients/me/profile', { email_notification_enabled: true })
    check(`${mode} patient re-verifies a new address (+fail test hook) and enables notifications`, sent.status === 200 && confirmed.status === 200
      && enabled.body.data.email_notification_enabled === true, `${sent.status} ${confirmed.status}`)
    shapes[mode].verification = shape(sent.body)
    await signOut(p)
    await signIn(p, 'nurse01@demo.local')
    await go(p, `/nurse/patients/${pid}`)
    await waitSel(p, '[data-new-reminder]')
    await click(p, '[data-new-reminder]')
    await setValue(p, '[data-reminder-form] input[name=title]', `寄信失敗測試 ${mode}`)
    await setValue(p, '[data-reminder-form] textarea[name=message]', '這則通知的 Email 會寄送失敗')
    await click(p, '[data-reminder-form] button[type=submit]')
    check(`${mode} nurse: email failure → app notification still sent, failure shown`, await waitText(p, '已送出，病人可在「通知」看到') && await waitText(p, 'Email 寄送失敗'))
    check(`${mode} the failed-email notification is new (not rolled back)`, await waitFn(p, (t) =>
      [...document.querySelectorAll('[data-sent-list] [data-reminder]')].some((li) => li.innerText.includes(t) && li.dataset.status === 'new'
        && li.querySelector('[data-email-delivery]')?.dataset.emailStatus === 'failed'), `寄信失敗測試 ${mode}`))
  }

  // handling lifecycle unchanged: 接手
  await p.evaluate((t) => [...document.querySelectorAll('[data-sent-list] [data-reminder]')].find((li) => li.innerText.includes(t))?.querySelector('[data-step=acknowledge]')?.click(), `${TITLE} ${mode}`)
  check(`${mode} nurse: 接手 still works (new → acknowledged), email status unchanged`, await waitFn(p, (t) =>
    [...document.querySelectorAll('[data-sent-list] [data-reminder]')].some((li) => li.innerText.includes(t) && li.dataset.status === 'acknowledged'
      && li.querySelector('[data-email-delivery]')), `${TITLE} ${mode}`))
  await signOut(p)

  // ---------------------------------------------------------------- patient receives the app notification
  await p.setViewport(phone)
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/notifications')
  const received = await waitText(p, `${TITLE} ${mode}`)
  await p.evaluate((t) => [...document.querySelectorAll('[data-notification] button')].find((b) => b.innerText.includes(t))?.click(), `${TITLE} ${mode}`)
  check(`${mode} patient: app notification received with the full content`, received && await waitText(p, MESSAGE))
  if (emailing) check(`${mode} patient: the notification whose email failed is there too`, await waitText(p, `寄信失敗測試 ${mode}`))
  check(`${mode} patient: notification page shows no delivery internals`, !(await text(p)).includes('Email 寄送失敗'))
  await signOut(p)

  // ---------------------------------------------------------------- API only: audit, timeline, risk
  if (mode === 'API') {
    const at = await tokenFor('admin01@demo.local')
    const audit = (await api(`/admin/audit-logs?patient_id=${pid}&per_page=200`, at)).body.data
    const blob = JSON.stringify(audit)
    check('API audit: profile, verification and delivery changes recorded', audit.some((a) => a.resource_type === 'patient_contacts')
      && audit.some((a) => a.resource_type === 'patient_profiles' && a.actor?.role === 'patient')
      && (!emailing || audit.some((a) => a.resource_type === 'notification_deliveries')))
    check('API audit: no email address, verification link / token or email text', !blob.includes('@example.test') && !blob.includes('#token=')
      && !blob.includes('verify-email#') && !blob.includes('您有一則來自護理團隊的新通知'))
    check('API audit: who entered the weight (CREATE vital_signs by the patient)', audit.some((a) => a.resource_type === 'vital_signs' && a.action === 'CREATE' && a.actor?.role === 'patient'))
    const types = new Set((await api(`/patients/${pid}/timeline?limit=100`, nt)).body.data.map((e) => e.event_type))
    const KNOWN = ['CHEMOTHERAPY', 'SYMPTOM', 'VITAL_SIGN', 'LAB_RESULT', 'NOTIFICATION', 'NOTIFICATION_STATUS', 'NURSING_ASSESSMENT', 'APPOINTMENT']
    check('API timeline: no new event types (emails are not timeline events)', [...types].every((t) => KNOWN.includes(t)), [...types].join(','))
    const dash = (await api(`/dashboard/patient/${pid}`, nt))
    check('API Risk Engine still answers, reading the patient-entered weight', dash.status === 200 && dash.body.data.widgets['latest-vitals'].weight_kg.value === 75
      && typeof dash.body.data.widgets['nurse-view'].risk.level === 'string')
  }
  await ctx.close()
}

for (const k of ['profile', 'weights', 'staffProfile', 'reminder', ...(PROD ? [] : ['verification'])]) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
