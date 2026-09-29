// Sprint 6 — patient pages at 390 px, in mock mode and API mode (patient01): 通知 (unread,
// open → read, 全部已讀, badge), 症狀回報 (report + 我的回報), 我的 (profile, 修改密碼, 登出),
// no staff names anywhere, no horizontal scroll, mock / API response structure.
import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
const phone = { width: 390, height: 1400, deviceScaleFactor: 1 }

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p) => p.evaluate(() => document.body.innerText)
const path = (p) => p.evaluate(() => location.pathname)
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitText = (p, s, timeout = 10000) => p.waitForFunction((s) => document.body.innerText.includes(s), { timeout }, s).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const nav = (p, label) => p.evaluate((label) => [...document.querySelectorAll('nav[aria-label="主要功能"] a')].find((a) => a.innerText.includes(label)).click(), label)
const badge = (p) => p.evaluate(() => {
  const a = [...document.querySelectorAll('nav[aria-label="主要功能"] a')].find((x) => x.innerText.includes('通知'))
  return Number((a.innerText.match(/\d+/) ?? ['0'])[0])
})
const clickText = (p, label) => p.evaluate((label) => [...document.querySelectorAll('button, label')].find((b) => b.innerText.trim() === label).click(), label)
const answer = (p, question, value) => p.evaluate((question, value) => {
  const fs = [...document.querySelectorAll('fieldset')].find((f) => f.querySelector('legend').innerText.includes(question))
  ;[...fs.querySelectorAll('label')].find((l) => l.innerText.trim() === String(value)).click()
}, question, value)
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
  if (mode === 'API') {
    // one more unread alert for the patient (fever) so there is something to read
    const pt = await tokenFor('patient01@demo.local')
    await api('/vital-signs', pt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ patient_id: 'me', temperature_c: 38.6 }) })
  }
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  await setValue(p, 'input[name=email]', 'patient01@demo.local')
  await setValue(p, 'input[name=password]', 'Demo@1234')
  await p.click('button[type=submit]')
  await p.waitForFunction(() => location.pathname === '/patient')
  await idle(p)

  // ---------------------------------------------------------------- 通知
  const homeBadge = await badge(p)
  await nav(p, '通知')
  check(`${mode} patient: 通知 opens the notification page`, await p.waitForFunction(() => location.pathname === '/patient/notifications').then(() => true, () => false)
    && await waitSel(p, '[data-patient-notifications] [data-notification]'))
  const unread0 = await p.$eval('[data-unread]', (e) => Number(e.innerText)).catch(() => 0)
  check(`${mode} patient: unread count shown and matches the badge`, unread0 >= 1 && unread0 === homeBadge && (await badge(p)) === unread0, `${unread0} / ${homeBadge}`)
  const first = await p.$eval('[data-notification][data-read="false"]', (e) => e.dataset.notification)
  await p.$eval(`[data-notification="${first}"] button`, (b) => b.click())
  check(`${mode} patient: opening shows the message and marks it read`, await waitSel(p, `[data-notification="${first}"] [data-notification-detail]`)
    && await p.waitForFunction((id) => document.querySelector(`[data-notification="${id}"]`)?.dataset.read === 'true', { timeout: 8000 }, first).then(() => true, () => false))
  check(`${mode} patient: unread count went down by one`, await p.waitForFunction((n) => Number(document.querySelector('[data-unread]')?.innerText ?? 0) === n || (n === 0 && !document.querySelector('[data-unread]')), { timeout: 5000 }, unread0 - 1).then(() => true, () => false))
  if (await p.$('[data-read-all]')) {
    await p.$eval('[data-read-all]', (b) => b.click())
    check(`${mode} patient: 全部已讀 → no unread, badge cleared`, await waitText(p, '全部都讀過了') && (await badge(p)) === 0 && !(await p.$('[data-notification][data-read="false"]')))
  }
  check(`${mode} patient: notifications show no staff names or internal notes`, !(await text(p)).includes('測試護理師') && !(await text(p)).includes('內部'))
  check(`${mode} 390px: 通知 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-patient-notifications.png`, fullPage: true })

  // ---------------------------------------------------------------- 症狀回報
  await nav(p, '症狀回報')
  check(`${mode} patient: 症狀回報 page with the report and 我的回報`, await p.waitForFunction(() => location.pathname === '/patient/symptoms').then(() => true, () => false)
    && await waitSel(p, '[data-my-reports][data-loaded="true"]') && await waitText(p, '今天的症狀'))
  const before = (await p.$$('[data-my-reports] [data-report]')).length
  await clickText(p, '開始回報')
  await p.waitForFunction(() => document.querySelectorAll('fieldset').length >= 4, { timeout: 8000 })
  await answer(p, '疼痛', 2)
  await answer(p, '噁心', 1)
  await answer(p, '疲倦', 2)
  await answer(p, '發燒', '沒有')
  await clickText(p, '送出回報')
  check(`${mode} patient: report sent and added to 我的回報`, await waitText(p, '已送出，謝謝您')
    && await p.waitForFunction((n) => document.querySelectorAll('[data-my-reports] [data-report]').length === n + 1, { timeout: 8000 }, before).then(() => true, () => false))
  check(`${mode} patient: 我的回報 shows no reviewer names`, !(await p.$eval('[data-my-reports]', (e) => e.innerText)).includes('測試護理師'))
  check(`${mode} 390px: 症狀回報 has no horizontal overflow`, await noOverflow(p))

  // ---------------------------------------------------------------- 我的
  await nav(p, '我的')
  check(`${mode} patient: 我的 shows own profile (code, allergy alert) and hospital contact`, await waitSel(p, '[data-patient-profile] [data-logout]')
    && await waitText(p, 'P00001') && (await text(p)).includes('Penicillin') && (await text(p)).includes('請假專線'))
  check(`${mode} patient: profile has no care team / nurse names`, !(await text(p)).includes('測試護理師'))
  check(`${mode} 390px: 我的 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-patient-profile.png`, fullPage: true })
  await p.$eval('[data-change-password-link]', (a) => a.click())
  check(`${mode} patient: 修改密碼 opens the password page (voluntary, with 返回)`, await p.waitForFunction(() => location.pathname === '/change-password').then(() => true, () => false)
    && !!(await p.$('[data-cancel-change]')))
  await p.$eval('[data-cancel-change]', (b) => b.click())
  await p.waitForFunction(() => location.pathname === '/patient/me')

  // ---------------------------------------------------------------- shapes (before signing out)
  if (mode === 'API') {
    const t = await tokenFor('patient01@demo.local')
    shapes.API.list = shape((await api('/notifications?per_page=50', t)).body)
    shapes.API.unread = shape((await api('/notifications/unread-count', t)).body)
    shapes.API.readAll = shape((await api('/notifications/read-all', t, { method: 'POST' })).body)
  } else {
    const m = await p.evaluate(async () => {
      const mock = await import('/src/mock/api.js')
      return { list: mock.mockMyNotifications(), unread: mock.mockMyUnreadCount(), readAll: mock.mockMyReadAll() }
    })
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
  }
  await p.$eval('[data-logout]', (b) => b.click())
  check(`${mode} patient: 登出 returns to the login page`, await p.waitForFunction(() => location.pathname === '/login').then(() => true, () => false) && await waitText(p, '您已登出'))
  await ctx.close()
}

for (const k of ['list', 'unread', 'readAll']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
