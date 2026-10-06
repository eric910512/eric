// Email content policy for nurse-sent notifications, in mock mode and API mode: the nurse picks a
// topic and an email mode (不寄 Email / 寄送摘要 / 寄送標題與內容) on the patient page → sensitive topics
// only offer 摘要 (with the privacy notice) → a non-sensitive topic sends title + content → a sensitive
// topic sends a generic summary → a direct API / handler call asking for full on a sensitive topic is
// downgraded by the backend → 不寄 Email sends nothing → HTML escaped, nurse URLs not linked → the
// patient still gets the full app notification → mock / API response structure.
// Production simulation (E2E_PROD, no email provider): API mode checks the UI and that every
// delivery is skipped / not_configured with the requested / applied modes recorded.
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
const GENERIC = '您有一則來自護理團隊的醫療照護通知'
const NOTICE = '此類通知可能包含敏感醫療資訊，為保護病人隱私，Email 僅提供通知摘要，完整內容請病人登入 App 查看。'

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

async function outbox(mode, p) {
  if (mode === 'MOCK') return p.evaluate(() => JSON.parse(sessionStorage.getItem('ccp.mock.email-outbox') ?? '[]'))
  if (!OUTBOX_DIR || !fs.existsSync(OUTBOX_DIR)) return []
  return fs.readdirSync(OUTBOX_DIR).sort().map((f) => JSON.parse(fs.readFileSync(path.join(OUTBOX_DIR, f), 'utf8')))
}
const notificationEmails = async (mode, p) => (await outbox(mode, p)).filter((m) => m.purpose === 'notification')

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const emailing = mode === 'MOCK' || !PROD
  const ADDRESS = `modes.${mode.toLowerCase()}@example.test`
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  let pid = MOCK_P1
  let nt, pt
  if (mode === 'API') {
    nt = await tokenFor('nurse01@demo.local')
    pt = await tokenFor('patient01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
  }
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message, details: e.details } } }
    }
  }, { name, args })
  const call = (token, name, args, method, url, body) => (mode === 'API'
    ? api(url, token, { method, body: body ? JSON.stringify(body) : undefined })
    : mockCall(name, args))

  // ---------------------------------------------------------------- patient: verified email, notifications on
  await signIn(p, 'patient01@demo.local')
  if (emailing) {
    await call(pt, 'mockUpdateProfile', [pid, { email: ADDRESS }], 'PATCH', '/patients/me/profile', { email: ADDRESS })
    await call(pt, 'mockRequestEmailVerification', [pid], 'POST', '/patients/me/email-verification')
    const link = (await outbox(mode, p)).filter((m) => m.purpose === 'verification' && m.to === ADDRESS).at(-1)?.text.match(/#token=(\S+)/)?.[1]
    const confirmed = await call(pt, 'mockConfirmEmailVerification', [pid, { token: link }], 'POST', '/patients/me/email-verification/confirm', { token: link })
    const enabled = await call(pt, 'mockUpdateProfile', [pid, { email_notification_enabled: true }], 'PATCH', '/patients/me/profile', { email_notification_enabled: true })
    check(`${mode} setup: patient has a verified email with notifications on`, confirmed.status === 200 && enabled.body?.data?.email_notification_enabled === true)
  }
  await signOut(p)

  // ---------------------------------------------------------------- nurse UI
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  await waitSel(p, '[data-new-reminder]')
  await click(p, '[data-new-reminder]')
  const radios = () => p.$$eval('[data-email-mode] input[name=email_mode]', (r) => r.map((x) => x.value))
  check(`${mode} nurse: default topic 其他醫療相關 → only 不寄 Email / 寄送摘要, privacy notice shown`,
    await waitSel(p, '[data-email-mode]') && JSON.stringify(await radios()) === '["none","summary"]'
    && (await p.$eval('[data-sensitive-notice]', (e) => e.innerText)) === NOTICE
    && (await p.$eval('[data-email-mode] input[value=summary]', (r) => r.checked)))
  await p.select('[data-reminder-form] select[name=category]', 'schedule')
  check(`${mode} nurse: 行程與報到 → 寄送標題與內容 offered, no notice`, await waitFn(p, () => !!document.querySelector('[data-email-mode] input[value=full]'))
    && !(await p.$('[data-sensitive-notice]')))
  await click(p, '[data-email-mode] input[value=full]')
  await p.select('[data-reminder-form] select[name=category]', 'medication')
  check(`${mode} nurse: switching to 用藥與治療 removes 標題與內容 and falls back to 摘要`, await waitFn(p, () => !document.querySelector('[data-email-mode] input[value=full]'))
    && (await p.$eval('[data-email-mode] input[value=summary]', (r) => r.checked)) && !!(await p.$('[data-sensitive-notice]')))
  check(`${mode} nurse form: no horizontal overflow`, await noOverflow(p))

  // full, non-sensitive
  await p.select('[data-reminder-form] select[name=category]', 'schedule')
  await click(p, '[data-email-mode] input[value=full]')
  const FULL_TITLE = `下週回診 ${mode}`
  const FULL_MESSAGE = '下週三 10:00 回診，請攜帶健保卡。\n<b>注意</b> 詳見 https://evil.example/login'
  let before = (await notificationEmails(mode, p)).length
  await setValue(p, '[data-reminder-form] input[name=title]', FULL_TITLE)
  await setValue(p, '[data-reminder-form] textarea[name=message]', FULL_MESSAGE)
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: 寄送標題與內容 → notification sent, email ${emailing ? 'sent (標題與內容)' : 'not configured'}`,
    await waitText(p, '已送出，病人可在「通知」看到') && await waitFn(p, (a) => [...document.querySelectorAll('[data-sent-list] [data-reminder]')]
      .some((li) => li.innerText.includes(a.t) && li.dataset.status === 'new' && li.querySelector('[data-email-delivery]')?.dataset.emailMode === 'full'
        && li.querySelector('[data-email-delivery]')?.dataset.emailStatus === a.s), { t: FULL_TITLE, s: emailing ? 'sent' : 'skipped' }))
  if (emailing) {
    const m = (await notificationEmails(mode, p)).at(-1)
    check(`${mode} FULL email: subject 癌症照護系統｜{title}, title + content + time + sign-in`, (await notificationEmails(mode, p)).length === before + 1
      && m.subject === `癌症照護系統｜${FULL_TITLE}` && m.text.includes('下週三 10:00 回診，請攜帶健保卡。') && m.text.includes('發送時間：')
      && m.text.includes('登入癌症照護系統：'), m?.subject)
    check(`${mode} FULL email HTML: content escaped, nurse URL not a link, one system link`, m.html.includes('&lt;b&gt;注意&lt;/b&gt;')
      && !m.html.includes('href="https://evil') && (m.html.match(/<a /g) ?? []).length === 1 && m.html.includes('/patient/notifications"'))
  }

  // sensitive topic via the UI → generic summary
  await click(p, '[data-new-reminder]')
  await p.select('[data-reminder-form] select[name=category]', 'medication')
  const SENS_TITLE = `化療劑量調整 ${mode}`
  before = (await notificationEmails(mode, p)).length
  await setValue(p, '[data-reminder-form] input[name=title]', SENS_TITLE)
  await setValue(p, '[data-reminder-form] textarea[name=message]', '化療藥物劑量調整為 80%，WBC 2.1。')
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: sensitive topic → email mode 摘要`, await waitFn(p, (t) => [...document.querySelectorAll('[data-sent-list] [data-reminder]')]
    .some((li) => li.innerText.includes(t) && li.querySelector('[data-email-delivery]')?.dataset.emailMode === 'summary'), SENS_TITLE))
  if (emailing) {
    const m = (await notificationEmails(mode, p)).at(-1)
    check(`${mode} SUMMARY (sensitive): generic title, no original title / content`, (await notificationEmails(mode, p)).length === before + 1
      && m.subject === '癌症照護系統｜您有一則新通知' && m.text.includes(GENERIC) && !m.text.includes('化療劑量調整') && !m.text.includes('WBC')
      && !m.html.includes('WBC') && m.text.includes('為保護您的醫療資訊，完整內容請登入癌症照護系統查看。'))
  }

  // 不寄 Email
  await click(p, '[data-new-reminder]')
  await p.select('[data-reminder-form] select[name=category]', 'preparation')
  await click(p, '[data-email-mode] input[value=none]')
  const NONE_TITLE = `空腹提醒 ${mode}`
  before = (await notificationEmails(mode, p)).length
  await setValue(p, '[data-reminder-form] input[name=title]', NONE_TITLE)
  await setValue(p, '[data-reminder-form] textarea[name=message]', '明早 6 點後請空腹。')
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: 不寄 Email → 未寄（選擇不寄 Email）, nothing sent`, await waitText(p, '選擇不寄 Email')
    && (await notificationEmails(mode, p)).length === before)

  // direct call asking for full on a sensitive topic → backend / handler downgrades
  before = (await notificationEmails(mode, p)).length
  const direct = await call(nt, 'mockCreateReminder', [{ patient_id: pid, title: '檢驗結果', message: 'ANC 0.8', category: 'symptom_followup', email_mode: 'full' }],
    'POST', '/notifications', { patient_id: pid, title: '檢驗結果', message: 'ANC 0.8', category: 'symptom_followup', email_mode: 'full' })
  const e = direct.body?.data?.email_delivery
  check(`${mode} direct call: sensitive topic + full → 201-equivalent, downgraded to summary`, (direct.status === 201 || direct.status === 200)
    && e?.mode_requested === 'full' && e?.mode === 'summary' && e?.downgraded === true && e?.category === 'symptom_followup', JSON.stringify(e))
  if (emailing) {
    const m = (await notificationEmails(mode, p)).at(-1)
    check(`${mode} direct call email: no title / content`, (await notificationEmails(mode, p)).length === before + 1 && !m.text.includes('ANC') && m.text.includes(GENERIC))
  } else {
    check(`${mode} (production): delivery skipped / not_configured with modes recorded`, e?.status === 'skipped' && e?.skip_reason === 'not_configured')
  }
  shapes[mode].reminder = shape(direct.body?.data)
  const invalid = await call(nt, 'mockCreateReminder', [{ patient_id: pid, title: 'x', message: 'y', email_mode: 'everything' }],
    'POST', '/notifications', { patient_id: pid, title: 'x', message: 'y', email_mode: 'everything' })
  check(`${mode} invalid email_mode → 400`, invalid.status === 400, invalid.status)
  shapes[mode].invalid = { code: invalid.body?.error?.code, field: invalid.body?.error?.details?.[0]?.field, issue: invalid.body?.error?.details?.[0]?.issue }
  await signOut(p)

  // ---------------------------------------------------------------- patient: full app content regardless of the email
  await p.setViewport(phone)
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/notifications')
  const got = await waitText(p, SENS_TITLE)
  await p.evaluate((t) => [...document.querySelectorAll('[data-notification] button')].find((b) => b.innerText.includes(t))?.click(), SENS_TITLE)
  check(`${mode} patient: app notification keeps the full content (sensitive topic)`, got && await waitText(p, '化療藥物劑量調整為 80%，WBC 2.1。'))
  check(`${mode} patient: no email-mode internals on the patient page`, !(await text(p)).includes('敏感主題已改為摘要') && !(await text(p)).includes('主題：'))
  await signOut(p)
  await ctx.close()
}

check('mock / API invalid email_mode: same error code, field and issue', JSON.stringify(shapes.API.invalid) === JSON.stringify(shapes.MOCK.invalid)
  && shapes.API.invalid?.code === 'VALIDATION_ERROR' && shapes.API.invalid?.field === 'email_mode', `${JSON.stringify(shapes.API.invalid)} vs ${JSON.stringify(shapes.MOCK.invalid)}`)
for (const k of ['reminder']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
