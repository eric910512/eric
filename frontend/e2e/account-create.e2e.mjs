// Regression: 「建立登入帳號」 on the patient detail page, in mock mode and API mode. Commit 50317a1 put
// the notification-contact block between the account `v-if` (existing account) and its `v-else`
// (create form), so the form never appeared for a patient without an account. Checks: an admin opens
// a patient without an account → 尚未建立登入帳號 + [建立登入帳號] → validation / duplicate email →
// account created through the existing POST /patients/{pid}/account → one-time password shown, copy,
// close → account details replace the form → a patient with an account keeps the original UI →
// notification contact shown in both cases → the new account must set a new password on first login →
// mock / API response structure.
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
const MOCK = { withAccount: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0', noAccount: 'd2e3f4a5-6b7c-4d8e-9f0a-1b2c3d4e5f60', other: 'e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a80' }
const NEW_PASSWORD = 'Patient2Pass88'

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const login = async (email, password = 'Demo@1234') => api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })
const tokenFor = async (email) => (await login(email)).body.data.access_token
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
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
const signOut = async (p) => {
  await p.evaluate(() => [...document.querySelectorAll('button, a')].find((b) => b.innerText.trim() === '登出')?.click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
}
function shape(v) {
  if (Array.isArray(v)) return v.length ? [shape(v[0])] : []
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, shape(v[k])]))
  return v === null ? null : typeof v
}
const shapes = {}

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const NEW_EMAIL = `patient.new.${mode.toLowerCase()}@demo.local`
  const ctx = await browser.createBrowserContext()
  await ctx.overridePermissions(base, ['clipboard-read', 'clipboard-write', 'clipboard-sanitized-write'])
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  let ids = MOCK
  let at
  if (mode === 'API') {
    at = await tokenFor('admin01@demo.local')
    const create = (name) => api('/patients', at, { method: 'POST', body: JSON.stringify({ display_name: name, gender: 'female', date_of_birth: '1964-02-18' }) })
    ids = {
      withAccount: (await api('/patients?q=P00001', at)).body.data[0].id,
      noAccount: (await create('測試病人 帳號')).body.data.id,
      other: (await create('測試病人 結構')).body.data.id,
    }
  }

  // ---------------------------------------------------------------- admin: patient without an account
  await signIn(p, 'admin01@demo.local')
  await go(p, `/admin/patients/${ids.noAccount}`)
  check(`${mode} admin: patient without an account shows 尚未建立登入帳號 + [建立登入帳號]`, await waitSel(p, '[data-account] [data-no-account] [data-open-account-form]')
    && (await text(p, '[data-account]')).includes('尚未建立登入帳號') && !(await p.$('[data-account] [data-account-info]')))
  check(`${mode} admin: notification contact still shown (no account: 未設定 / 未驗證 / 關閉)`, await waitSel(p, '[data-account] [data-notification-contact]')
    && (await text(p, '[data-contact-email]')) === '未設定' && (await text(p, '[data-contact-verified]')) === '未驗證' && (await text(p, '[data-contact-enabled]')) === '關閉')
  await click(p, '[data-open-account-form]')
  check(`${mode} admin: [建立登入帳號] opens the email form`, await waitSel(p, '[data-account-form] input[name=email]') && !(await p.$('[data-open-account-form]')))
  await setValue(p, '[data-account-form] input[name=email]', 'not-an-email')
  await click(p, '[data-account-form] button[type=submit]')
  check(`${mode} admin: invalid email → 請輸入正確的 email`, await waitFn(p, () => document.querySelector('[data-account-error]')?.innerText === '請輸入正確的 email'))
  await setValue(p, '[data-account-form] input[name=email]', 'patient01@demo.local')
  await click(p, '[data-account-form] button[type=submit]')
  check(`${mode} admin: email already used → 這個 email 已經有帳號 (no account created)`, await waitFn(p, () => document.querySelector('[data-account-error]')?.innerText === '這個 email 已經有帳號')
    && !!(await p.$('[data-no-account]')))
  await setValue(p, '[data-account-form] input[name=email]', NEW_EMAIL)
  await click(p, '[data-account-form] button[type=submit]')
  check(`${mode} admin: 建立帳號 → one-time password panel with the login email`, await waitSel(p, '[data-otp-password]')
    && (await text(p, '[data-otp-email]')) === NEW_EMAIL)
  const temporary = (await text(p, '[data-otp-password]')).trim()
  check(`${mode} temporary password shown once (non-empty)`, temporary.length >= 8, `${temporary.length} chars`)
  check(`${mode} account details replace the create form (初始密碼 state)`, await waitSel(p, '[data-account-info]') && !(await p.$('[data-no-account]'))
    && (await text(p, '[data-account-email]')) === NEW_EMAIL && (await text(p, '[data-account-state]')).includes('尚未設定新密碼'))
  check(`${mode} notification contact still shown after the account exists`, !!(await p.$('[data-account] [data-notification-contact]')))
  await p.bringToFront() // the clipboard needs a focused page and a real click
  const copyButton = await p.evaluateHandle(() => [...document.querySelectorAll('[data-account] button')].find((b) => b.innerText.includes('複製密碼')))
  await copyButton.asElement()?.click()
  const copiedShown = await waitText(p, '已複製')
  const clip = await p.evaluate(() => navigator.clipboard.readText().then((t) => t, (e) => `ERR ${e.name}: ${e.message}`))
  check(`${mode} 複製密碼 → copied`, copiedShown && clip === temporary, `shown=${copiedShown} clipboard=${clip === temporary ? 'match' : clip.slice(0, 80)}`)
  await click(p, '[data-otp-done]')
  check(`${mode} closing the panel hides the password for good`, await waitFn(p, () => !document.querySelector('[data-otp-password]'))
    && !(await text(p)).includes(temporary))
  await p.reload({ waitUntil: 'networkidle0' })
  check(`${mode} after reload: account shown, no password, no create form`, await waitSel(p, '[data-account-info]') && !(await p.$('[data-otp-password]')) && !(await p.$('[data-no-account]')))

  // ---------------------------------------------------------------- patient with an account: original UI
  await go(p, `/admin/patients/${ids.withAccount}`)
  check(`${mode} patient with an account keeps the account details (no create UI)`, await waitSel(p, '[data-account-info]')
    && (await text(p, '[data-account-email]')) === 'patient01@demo.local' && !(await p.$('[data-no-account]')) && !(await p.$('[data-account-form]')))
  check(`${mode} …and its notification contact`, !!(await p.$('[data-account] [data-notification-contact]')))
  check(`${mode} 390px-ready: no horizontal overflow on the patient page`, await noOverflow(p))

  // shapes of the existing endpoint (another patient without an account)
  const res = mode === 'API'
    ? await api(`/patients/${ids.other}/account`, at, { method: 'POST', body: JSON.stringify({ email: `shape.${mode.toLowerCase()}@demo.local` }) })
    : await p.evaluate(async (a) => ({ status: 201, body: await (await import('/src/mock/api.js')).mockCreateAccount(a.id, { email: a.email }) }),
      { id: ids.other, email: `shape.${mode.toLowerCase()}@demo.local` })
  shapes[mode] = shape(res.body)
  check(`${mode} existing endpoint: 201 with temporary password, must_change_password`, res.status === 201 && res.body.data.must_change_password === true
    && typeof res.body.data.temporary_password === 'string')
  await signOut(p)

  // ---------------------------------------------------------------- first login: set a new password
  await p.setViewport(phone)
  if (mode === 'API') {
    const t = (await login(NEW_EMAIL, temporary)).body?.data
    check('API: the new account signs in with the temporary password, must_change_password', t?.user?.must_change_password === true, JSON.stringify(t?.user))
    const blocked = await api('/patients/me', t?.access_token)
    check('API: before a new password every other API → 403 PASSWORD_CHANGE_REQUIRED', blocked.status === 403 && blocked.body.error.code === 'PASSWORD_CHANGE_REQUIRED')
  }
  await signIn(p, NEW_EMAIL, temporary)
  check(`${mode} first login → 設定新密碼 (required)`, await waitFn(p, () => location.pathname === '/change-password') && await waitText(p, '設定新密碼'))
  await setValue(p, 'input[name=current_password]', temporary)
  await setValue(p, 'input[name=new_password]', NEW_PASSWORD)
  await setValue(p, 'input[name=confirm_password]', NEW_PASSWORD)
  await click(p, '[data-change-password] button[type=submit]')
  check(`${mode} new password set → patient home`, await waitFn(p, () => location.pathname === '/patient'))
  await signOut(p)
  await signIn(p, NEW_EMAIL, NEW_PASSWORD)
  check(`${mode} signs in with the new password, no forced change`, await waitFn(p, () => location.pathname === '/patient'))
  await ctx.close()
}

const diff = JSON.stringify(shapes.API) === JSON.stringify(shapes.MOCK)
check('mock / API create-account response: same structure', shapes.API && shapes.MOCK && diff, `${JSON.stringify(shapes.API)} vs ${JSON.stringify(shapes.MOCK)}`)
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
