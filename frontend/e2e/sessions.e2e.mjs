// Authentication hardening — server-side sessions, in mock mode and API mode: 登入裝置 lists every
// sign-in (this device marked) → 登出其他所有裝置 ends the others at once → 登出 ends this session
// on the server (the old token is rejected) → password change ends the other sessions → admin
// 強制登出 and 重設密碼 (one-time temporary password, first-login flow) → 390 px → audit without
// secrets → mock / API response structure.
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
const desk = { width: 1280, height: 1000, deviceScaleFactor: 1 }
const phone = { width: 390, height: 1200, deviceScaleFactor: 1 }
const TEMP = /^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$/

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email, password = 'Demo@1234') =>
  (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })).body?.data?.access_token
const meStatus = async (t) => (await api('/auth/me', t)).status
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
async function signIn(p, email, password = 'Demo@1234') {
  await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', password)
  await p.click('button[type=submit]')
  await idle(p)
}
const headerLogout = (p) => p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
const storedToken = (p) => p.evaluate(() => JSON.parse(sessionStorage.getItem('ccp.auth'))?.token ?? null)
const liveMockSessions = (p, email) => p.evaluate((email) => {
  const s = JSON.parse(sessionStorage.getItem('ccp.mock.care-team'))
  const id = s.accounts[email].id
  return Object.values(s.sessions ?? {}).filter((x) => x.user_id === id && !x.revoked_at).length
}, email)
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
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  // ---------------------------------------------------------------- patient: 登入裝置 (390 px)
  await signIn(p, 'patient01@demo.local')
  await waitFn(p, () => location.pathname === '/patient')
  // a second sign-in elsewhere (another device)
  let other = null
  if (mode === 'API') other = await tokenFor('patient01@demo.local')
  else await p.evaluate(async () => (await import('/src/mock/api.js')).mockSignIn('patient01@demo.local', 'Demo@1234', 'Mozilla/5.0 (iPhone) Safari/605'))
  await go(p, '/patient/me')
  await click(p, '[data-profile-sessions]')
  check(`${mode} 登入裝置: opens from 我的, lists both sign-ins, this device marked`, await waitFn(p, () => location.pathname === '/account/sessions')
    && await waitFn(p, () => document.querySelectorAll('[data-session]').length >= 2) && (await p.$$('[data-session][data-current="true"]')).length === 1)
  check(`${mode} 390px: 登入裝置 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-sessions.png`, fullPage: true })
  if (mode === 'API') {
    const pt = await storedToken(p)
    shapes.API.list = shape((await api('/auth/sessions', pt)).body)
  } else {
    shapes.MOCK.list = shape(await p.evaluate(async () => (await import('/src/mock/api.js')).mockListSessions()))
  }
  await click(p, '[data-end-others]')
  check(`${mode} 登出其他所有裝置 → only this device remains`, await waitText(p, '已登出其他') && await waitFn(p, () => document.querySelectorAll('[data-session]').length === 1))
  if (mode === 'API') check('API: the other device\'s token is rejected at once', (await meStatus(other)) === 401)
  else check('MOCK: the other mock session is ended', (await liveMockSessions(p, 'patient01@demo.local')) === 1)

  // 登出 ends this session on the server
  const mine = await storedToken(p)
  await headerLogout(p)
  await waitFn(p, () => location.pathname.startsWith('/login'))
  if (mode === 'API') {
    await new Promise((r) => setTimeout(r, 500)) // the sign-out request is sent in the background
    check('API 登出: the signed-out token no longer works', (await meStatus(mine)) === 401)
  } else {
    check('MOCK 登出: the mock session is ended', await waitFn(p, (e) => {
      const s = JSON.parse(sessionStorage.getItem('ccp.mock.care-team'))
      const id = s.accounts[e].id
      return Object.values(s.sessions).filter((x) => x.user_id === id && !x.revoked_at).length === 0
    }, 'patient01@demo.local'))
  }

  // ---------------------------------------------------------------- password change ends the other sessions
  await p.setViewport(desk)
  await signIn(p, 'patient01@demo.local')
  if (mode === 'API') other = await tokenFor('patient01@demo.local')
  else await p.evaluate(async () => (await import('/src/mock/api.js')).mockSignIn('patient01@demo.local', 'Demo@1234'))
  await go(p, '/change-password')
  await p.waitForSelector('input[name=current_password]')
  await setValue(p, 'input[name=current_password]', 'Demo@1234')
  await setValue(p, 'input[name=new_password]', 'Session2026ok')
  await setValue(p, 'input[name=confirm_password]', 'Session2026ok')
  await p.$eval('form button[type=submit]', (b) => b.click())
  await idle(p)
  const changed = await waitFn(p, () => !location.pathname.startsWith('/change-password'), null, 8000)
  if (mode === 'API') {
    check('API password change: other sessions end, this one keeps working', changed && (await meStatus(other)) === 401 && (await meStatus(await storedToken(p))) === 200)
  } else {
    check('MOCK password change: other mock sessions end, this one keeps working', changed && (await liveMockSessions(p, 'patient01@demo.local')) === 1)
  }
  await headerLogout(p)
  await waitFn(p, () => location.pathname.startsWith('/login'))

  // ---------------------------------------------------------------- admin: 強制登出, 重設密碼
  let nurseToken = null
  if (mode === 'API') nurseToken = await tokenFor('nurse01@demo.local')
  else await p.evaluate(async () => (await import('/src/mock/api.js')).mockSignIn('nurse01@demo.local', 'Demo@1234'))
  await signIn(p, 'admin01@demo.local')
  await go(p, '/admin/accounts')
  await waitSel(p, '[data-account="nurse01@demo.local"] [data-active-sessions]')
  check(`${mode} admin: account list shows the nurse's active sign-ins`, (await p.$eval('[data-account="nurse01@demo.local"] [data-active-sessions]', (e) => e.innerText)).includes('個裝置登入中'))
  const nurseId = mode === 'API'
    ? (await api('/admin/users?role=nurse', await storedToken(p))).body.data.find((u) => u.email === 'nurse01@demo.local').id
    : await p.evaluate(() => JSON.parse(sessionStorage.getItem('ccp.mock.care-team')).accounts['nurse01@demo.local'].id)
  if (mode === 'API') shapes.API.adminSessions = shape((await api(`/admin/users/${nurseId}/sessions`, await storedToken(p))).body)
  else shapes.MOCK.adminSessions = shape(await p.evaluate(async (id) => (await import('/src/mock/api.js')).mockUserSessions(id), nurseId))
  await click(p, '[data-account="nurse01@demo.local"] [data-revoke-sessions]')
  check(`${mode} admin 強制登出 → notice, badge gone`, await waitText(p, '已強制登出') && await waitFn(p, () => !document.querySelector('[data-account="nurse01@demo.local"] [data-active-sessions]')))
  if (mode === 'API') check('API 強制登出: the nurse token is rejected at once', (await meStatus(nurseToken)) === 401)
  else check('MOCK 強制登出: the nurse mock sessions ended', (await liveMockSessions(p, 'nurse01@demo.local')) === 0)
  check(`${mode} admin: own row has no 強制登出 / 重設密碼`, !(await p.$('[data-account="admin01@demo.local"] [data-revoke-sessions], [data-account="admin01@demo.local"] [data-reset-password]')))
  await click(p, '[data-account="nurse01@demo.local"] [data-reset-password]')
  await click(p, '[data-account="nurse01@demo.local"] [data-confirm-reset]')
  check(`${mode} admin 重設密碼 → one-time temporary password`, await waitSel(p, '[data-otp-password]') && TEMP.test(await p.$eval('[data-otp-password]', (e) => e.innerText.trim())))
  const temp = await p.$eval('[data-otp-password]', (e) => e.innerText.trim())
  check(`${mode} admin: the temporary password is not kept in browser storage`, !(await p.evaluate((skip) => JSON.stringify([...Object.entries(sessionStorage).filter(([k]) => k !== skip), ...Object.entries(localStorage)]), 'ccp.mock.care-team')).includes(temp))
  await click(p, '[data-otp-done]')
  if (mode === 'API') {
    const at = await storedToken(p)
    const audit = JSON.stringify((await api('/admin/audit-logs?resource_type=users&per_page=200', at)).body)
    check('API audit: force sign-out and reset recorded, never the password', audit.includes('"forced":true') && audit.includes('"password":"reset"') && !audit.includes(temp))
  } else {
    const audit = await p.evaluate(async () => JSON.stringify((await import('/src/mock/api.js')).mockSearchAudit({ resource_type: 'users', per_page: 200 })))
    check('MOCK audit: force sign-out and reset recorded, never the password', audit.includes('"forced":true') && audit.includes('"password":"reset"') && !audit.includes(temp))
  }
  await headerLogout(p)
  await waitFn(p, () => location.pathname.startsWith('/login'))
  await signIn(p, 'nurse01@demo.local')
  check(`${mode} nurse: old password rejected after the reset`, await waitText(p, '帳號或密碼錯誤'))
  await signIn(p, 'nurse01@demo.local', temp)
  check(`${mode} nurse: temporary password → must set a new password`, await waitFn(p, () => location.pathname === '/change-password'))
  if (mode === 'API') {
    const t = await tokenFor('nurse01@demo.local', temp)
    const rs = await api('/admin/users/x/revoke-sessions', t, { method: 'POST' })
    check('API: non-admin cannot force sign-out (403)', rs.status === 403)
  }
  await ctx.close()
}

for (const k of ['list', 'adminSessions']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
