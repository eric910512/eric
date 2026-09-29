// Refresh token regression (Authentication Hardening), mock mode and API mode. The backend of this
// group issues 1-minute access tokens (JWT_ACCESS_TOKEN_MINUTES=1) so expiry is real:
//   1 sign in → 2 the access token expires → 3 refresh (proactive, and after a 401 TOKEN_EXPIRED)
//   → 4 the original API request succeeds → 5 sign out → 6 the old refresh token cannot refresh
//   → 7 a password change ends the other device's session → 8 an admin revoke ends it
//   → 9 page JavaScript cannot read the refresh token (HttpOnly cookie; nothing in web storage).
// API mode uses the real cookie through the same-origin /api proxy; mock mode the mock session.
import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 10000 }).catch(() => {})
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
const XHR = { 'X-Requested-With': 'XMLHttpRequest' }

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
/** Present a refresh token the way a browser would (Cookie header), straight to the backend. */
const refreshWith = (cookieValue) => api('/auth/refresh', null, { method: 'POST', headers: { ...XHR, Cookie: `refresh_token=${cookieValue}` } })
const tokenFor = async (email, password = 'Demo@1234') => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })).body?.data?.access_token
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const waitFn = (p, fn, arg, timeout = 10000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
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
const stored = (p) => p.evaluate(() => JSON.parse(sessionStorage.getItem('ccp.auth')))
/** Put an (expired) access token into the running app, like a tab whose token ran out. */
const useToken = (p, token, expiresAt = Date.now() - 1000) => p.evaluate(async (token, expiresAt, isMock) => {
  const auth = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('auth')
  auth.token = token
  auth.expiresAt = expiresAt
  sessionStorage.setItem('ccp.auth', JSON.stringify({ token, user: auth.user, expiresAt }))
  if (isMock) (await import('/src/mock/api.js')).setMockViewer(auth.user, token) // what the auth store does on sign-in
}, token, expiresAt, p.base === MOCK_URL)
// the cookie's Path is /api/v1/auth: ask for the cookies of that URL, not of the current page
const refreshCookie = async (p) => (await p.cookies(`${p.base}/api/v1/auth/refresh`)).find((c) => c.name === 'refresh_token')
const webStorage = (p) => p.evaluate(() => JSON.stringify({ s: { ...sessionStorage }, l: { ...localStorage }, c: document.cookie }))

async function newPage(base) {
  const ctx = await browser.createBrowserContext() // a separate browser: its own cookie jar and storage
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(e.message))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(m.text()))
  p.traffic = []
  p.base = base
  p.on('response', (r) => r.url().includes('/api/v1/') && p.traffic.push(`${r.request().method()} ${new URL(r.url()).pathname} ${r.status()}`))
  await p.setViewport({ width: 1280, height: 900 })
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  return p
}

// ================================================================ API mode: real cookie, real expiry
{
  const mode = 'API'
  console.log(`\n===== ${mode} =====`)
  const p = await newPage(WEB_URL)
  // 1. sign in
  await signIn(p, 'patient01@demo.local')
  check('API 1 sign in → access token in this tab, refresh token as a cookie', await waitFn(p, () => location.pathname === '/patient') && !!(await stored(p))?.token)
  const t0 = (await stored(p)).token
  const ck = await refreshCookie(p)
  check('API cookie: HttpOnly, Secure, SameSite=Strict, Path=/api/v1/auth, host-only on the frontend origin',
    ck && ck.httpOnly && ck.secure && ck.sameSite === 'Strict' && ck.path === '/api/v1/auth' && ck.domain === new URL(WEB_URL).hostname,
    JSON.stringify(ck && { httpOnly: ck.httpOnly, secure: ck.secure, sameSite: ck.sameSite, path: ck.path, domain: ck.domain }))
  // 9. page JavaScript cannot see it
  const seen = await webStorage(p)
  check('API 9 page JavaScript cannot read the refresh token (document.cookie, sessionStorage, localStorage)', !seen.includes(ck.value) && !seen.includes('refresh_token'))
  const loginBody = p.traffic.find((t) => t.startsWith('POST /api/v1/auth/login'))
  check('API login went through the frontend origin (same-origin /api proxy)', !!loginBody, p.traffic.slice(0, 3).join(' | '))

  // 2–3. real expiry: the tab renews the token before it runs out (proactive refresh)
  await sleep(40000)
  const t1 = (await stored(p)).token
  check('API 3 proactive refresh renewed the access token (cookie rotated)', t1 && t1 !== t0 && p.traffic.some((t) => t === 'POST /api/v1/auth/refresh 200')
    && (await refreshCookie(p))?.value !== ck.value)
  await sleep(26000) // t0 is now expired for real
  check('API 2 the first access token is expired for the backend', (await api('/auth/me', t0)).status === 401 && (await api('/auth/me', t0)).body.error.code === 'TOKEN_EXPIRED')
  // 3. the tab knows its token expired: navigation refreshes first, then loads the page
  await useToken(p, t0)
  p.traffic.length = 0
  await go(p, '/patient/timeline')
  let ok = await waitSel(p, 'section[data-timeline] li[data-event-type]')
  let seq = p.traffic.join(' | ')
  check('API 3 navigation with an expired token: POST /auth/refresh 200, then the page loads', ok && seq.startsWith('POST /api/v1/auth/refresh 200')
    && (await stored(p)).token !== t0, seq.slice(0, 200))
  // 3–4. the tab does not know yet: the request gets 401 TOKEN_EXPIRED → refresh → the same request again
  await useToken(p, t0, Date.now() + 60000)
  p.traffic.length = 0
  await go(p, '/patient/notifications')
  ok = await waitSel(p, '[data-patient-notifications]')
  await idle(p)
  seq = p.traffic.join(' | ')
  const failed = p.traffic.find((t) => t.endsWith(' 401'))
  check('API 3–4 request with an expired token: 401 → POST /auth/refresh 200 → the same request 200, page renders',
    ok && failed && seq.includes('POST /api/v1/auth/refresh 200') && seq.indexOf('POST /api/v1/auth/refresh 200') > seq.indexOf(failed)
    && p.traffic.filter((t) => t.startsWith(failed.replace(/ 401$/, ''))).some((t) => t.endsWith(' 200')) && (await stored(p)).token !== t0, seq.slice(0, 300))

  // 5–6. sign out → the old refresh token cannot refresh again
  const before = (await refreshCookie(p)).value
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
  await sleep(600)
  check('API 5 sign out clears the refresh cookie', !(await refreshCookie(p)))
  const replay = await refreshWith(before)
  check('API 6 the signed-out refresh token cannot refresh → 401 REFRESH_TOKEN_REVOKED', replay.status === 401 && replay.body.error.code === 'REFRESH_TOKEN_REVOKED', JSON.stringify(replay.body?.error))

  // 7. password change on one device → the other device's session cannot refresh
  const phone = await newPage(WEB_URL)
  const laptop = await newPage(WEB_URL)
  await signIn(phone, 'patient01@demo.local')
  await signIn(laptop, 'patient01@demo.local')
  const laptopCookie = (await refreshCookie(laptop)).value
  await go(phone, '/change-password')
  await phone.waitForSelector('input[name=current_password]')
  await setValue(phone, 'input[name=current_password]', 'Demo@1234')
  await setValue(phone, 'input[name=new_password]', 'Refresh2026ok')
  await setValue(phone, 'input[name=confirm_password]', 'Refresh2026ok')
  await phone.$eval('form button[type=submit]', (b) => b.click())
  await waitFn(phone, () => !location.pathname.startsWith('/change-password'))
  const r7 = await refreshWith(laptopCookie)
  check('API 7 after the password change the other device cannot refresh', r7.status === 401 && r7.body.error.code === 'REFRESH_TOKEN_REVOKED')
  await useToken(laptop, (await stored(laptop)).token)
  await laptop.reload({ waitUntil: 'networkidle0' })
  check('API 7 … and that device is sent to sign-in with 「登入已逾時」', await waitFn(laptop, () => location.pathname.startsWith('/login') && document.body.innerText.includes('登入已逾時')))
  await useToken(phone, (await stored(phone)).token)
  await phone.reload({ waitUntil: 'networkidle0' })
  check('API 7 the device that changed the password keeps its session (refresh works)', await waitFn(phone, () => location.pathname === '/patient'))

  // 8. admin revoke → cannot refresh
  const nurse = await newPage(WEB_URL)
  await signIn(nurse, 'nurse01@demo.local')
  const nurseCookie = (await refreshCookie(nurse)).value
  const at = await tokenFor('admin01@demo.local')
  const nurseId = (await api('/admin/users?role=nurse', at)).body.data.find((u) => u.email === 'nurse01@demo.local').id
  await api(`/admin/users/${nurseId}/revoke-sessions`, at, { method: 'POST' })
  const r8 = await refreshWith(nurseCookie)
  check('API 8 after an admin revoke the session cannot refresh', r8.status === 401 && r8.body.error.code === 'REFRESH_TOKEN_REVOKED')
  await go(nurse, '/nurse/patients')
  check('API 8 … and the nurse page goes back to sign-in', await waitFn(nurse, () => location.pathname.startsWith('/login')))
  check('API refresh without the CSRF header is refused', (await api('/auth/refresh', null, { method: 'POST', headers: { Cookie: `refresh_token=${nurseCookie}` } })).status === 403)
  for (const pg of [p, phone, laptop, nurse]) await pg.browserContext().close()
}

// ================================================================ mock mode: the same behaviour with the mock session
{
  const mode = 'MOCK'
  console.log(`\n===== ${mode} =====`)
  const p = await newPage(MOCK_URL)
  const mock = (fn, ...args) => p.evaluate(async (fn, args) => {
    const m = await import('/src/mock/api.js')
    try { return { ok: true, value: m[fn](...args) } } catch (e) { return { ok: false, status: e.status, code: e.code } }
  }, fn, args)
  await signIn(p, 'patient01@demo.local')
  check('MOCK 1 sign in', await waitFn(p, () => location.pathname === '/patient'))
  const t0 = (await stored(p)).token
  check('MOCK 9 no refresh token in document.cookie / web storage', !(await webStorage(p)).toLowerCase().includes('refresh'))
  // 2–4. the access token expires → the next request refreshes and continues
  const expiredToken = t0.replace(/~\d+$/, `~${Date.now() - 1000}`)
  await useToken(p, expiredToken)
  check('MOCK 2 an expired mock access token is refused (401 TOKEN_EXPIRED)', (await mock('mockMe')).code === 'TOKEN_EXPIRED')
  await go(p, '/patient/timeline')
  check('MOCK 3–4 navigation refreshes the session and the page renders', await waitSel(p, 'section[data-timeline] li[data-event-type]')
    && (await stored(p)).token !== expiredToken)
  await useToken(p, (await stored(p)).token.replace(/~\d+$/, `~${Date.now() - 1000}`), Date.now() + 60000) // expired, but the tab does not know yet
  const r = await p.evaluate(async () => {
    const { call } = await import('/src/api/call.js')
    return (await call('mockMyUnreadCount', [], null)).data.unread
  })
  check('MOCK 3–4 a call with an expired token: refresh, then the same call succeeds', typeof r === 'number' && Number((await stored(p)).token.split('~')[2]) > Date.now())
  // 5–6. sign out → the old session cannot refresh
  const beforeLogout = (await stored(p)).token
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
  await sleep(400)
  const r6 = await mock('mockRefresh', beforeLogout)
  check('MOCK 5–6 after sign-out the session cannot refresh (REFRESH_TOKEN_REVOKED)', !r6.ok && r6.code === 'REFRESH_TOKEN_REVOKED')
  // 7. password change ends the other device's session
  await signIn(p, 'patient01@demo.local')
  const other = await mock('mockSignIn', 'patient01@demo.local', 'Demo@1234', 'Other device')
  const otherToken = `mock-token-${other.value.user.id}~${other.value.sid}~${Date.now() + 60000}`
  await go(p, '/change-password')
  await p.waitForSelector('input[name=current_password]')
  await setValue(p, 'input[name=current_password]', 'Demo@1234')
  await setValue(p, 'input[name=new_password]', 'Refresh2026ok')
  await setValue(p, 'input[name=confirm_password]', 'Refresh2026ok')
  await p.$eval('form button[type=submit]', (b) => b.click())
  await waitFn(p, () => !location.pathname.startsWith('/change-password'))
  check('MOCK 7 after the password change the other device cannot refresh', (await mock('mockRefresh', otherToken)).code === 'REFRESH_TOKEN_REVOKED')
  check('MOCK 7 this device keeps refreshing', (await mock('mockRefresh', (await stored(p)).token)).ok)
  // 8. admin revoke
  const nurse = await mock('mockSignIn', 'nurse01@demo.local', 'Demo@1234', 'Nurse station')
  const nurseToken = `mock-token-${nurse.value.user.id}~${nurse.value.sid}~${Date.now() + 60000}`
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
  await signIn(p, 'admin01@demo.local')
  const rev = await mock('mockRevokeSessions', nurse.value.user.id)
  check('MOCK 8 after an admin revoke the session cannot refresh', rev.ok && (await mock('mockRefresh', nurseToken)).code === 'REFRESH_TOKEN_REVOKED')
  // the expired session is sent back to sign-in, like API mode
  await useToken(p, 'mock-token-x~no-such-session~1')
  await p.reload({ waitUntil: 'networkidle0' })
  check('MOCK an expired token whose session is over → sign-in with 「登入已逾時」', await waitFn(p, () => location.pathname.startsWith('/login') && document.body.innerText.includes('登入已逾時')))
  await p.browserContext().close()
}

check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
