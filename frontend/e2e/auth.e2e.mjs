import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })

async function fresh(base, viewport = { width: 1280, height: 900 }) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  await page.setViewport(viewport)
  page.base = base
  page.apiCalls = []
  page.on('request', (r) => r.url().includes('/api/v1/') && page.apiCalls.push(`${r.method()} ${new URL(r.url()).pathname} auth=${!!r.headers().authorization}`))
  return page
}
const go = (page, path) => page.goto(page.base + path, { waitUntil: 'networkidle0' })
const path = (page) => new URL(page.url()).pathname + new URL(page.url()).search
const text = (page) => page.evaluate(() => document.body.innerText)

async function login(page, email, password = 'Demo@1234') {
  await page.waitForSelector('input[name=email]')
  await page.$eval('input[name=email]', (el) => (el.value = ''))
  await page.type('input[name=email]', email)
  await page.$eval('input[name=password]', (el) => (el.value = ''))
  await page.type('input[name=password]', password)
  await Promise.all([page.click('button[type=submit]'), new Promise((r) => setTimeout(r, 1200))])
  await page.waitForNetworkIdle({ idleTime: 400 }).catch(() => {})
}

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} mode (${base}) =====`)

  // 1. guard: unauthenticated → login with redirect
  let p = await fresh(base)
  await go(p, '/patient')
  check(`${mode}: /patient unauthenticated → /login?redirect=/patient`, path(p).startsWith('/login') && (path(p).includes('redirect=/patient') || path(p).includes('redirect=%2Fpatient')), path(p))
  await go(p, '/')
  check(`${mode}: / unauthenticated → /login`, path(p).startsWith('/login'))

  // 2. wrong password
  await login(p, 'patient01@demo.local', 'wrong-password')
  check(`${mode}: wrong password shows error, stays on /login`,
    path(p).startsWith('/login') && (await text(p)).includes('帳號或密碼錯誤'))

  // 3. patient login → PatientDashboard
  await login(p, 'patient01@demo.local')
  await p.waitForFunction(() => document.body.innerText.includes('化療進度'), { timeout: 8000 }).catch(() => {})
  let body = await text(p)
  check(`${mode}: patient login → /patient with dashboard`, path(p) === '/patient' && body.includes('P00001') && body.includes('化療進度'), path(p))
  if (mode === 'API') {
    check('API: dashboard request uses /patient/me with Bearer token',
      p.apiCalls.some((c) => c === 'GET /api/v1/dashboard/patient/me auth=true'), p.apiCalls.filter((c) => c.includes('dashboard')).join(' | '))
  }
  await p.screenshot({ path: `${OUT}/e2e-${mode}-patient.png` })

  // 4. role guard: patient cannot open nurse page
  await go(p, '/nurse')
  check(`${mode}: patient → /nurse redirected to /patient`, path(p) === '/patient', path(p))
  // 5. reload keeps session (sessionStorage)
  await p.reload({ waitUntil: 'networkidle0' })
  check(`${mode}: reload keeps session`, path(p) === '/patient')
  // 6. logout
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
  await new Promise((r) => setTimeout(r, 500))
  check(`${mode}: logout → /login?reason=logout`, path(p).startsWith('/login') && (await text(p)).includes('您已登出'), path(p))
  await go(p, '/patient')
  check(`${mode}: after logout /patient requires login again`, path(p).startsWith('/login'))

  // 7. nurse login → NurseDashboard (redirect param honoured only if role allows)
  p = await fresh(base)
  await go(p, '/nurse')
  await login(p, 'nurse01@demo.local')
  await p.waitForFunction(() => document.body.innerText.includes('我的個案'), { timeout: 8000 }).catch(() => {})
  await p.waitForNetworkIdle({ idleTime: 500 }).catch(() => {})
  body = await text(p)
  check(`${mode}: nurse login → /nurse with caseload`, path(p).startsWith('/nurse') && body.includes('我的個案') && body.includes('P00001'), path(p))
  await new Promise((r) => setTimeout(r, 600))
  body = await text(p)
  check(`${mode}: nurse sees patient detail panel`, body.includes('最新生命徵象') && body.includes('症狀審閱'))
  if (mode === 'API') {
    check('API: nurse caseload fetched from backend with token',
      p.apiCalls.some((c) => c === 'GET /api/v1/dashboard/widgets/caseload/data auth=true'))
  }
  await p.screenshot({ path: `${OUT}/e2e-${mode}-nurse.png`, fullPage: false })
  await go(p, '/patient')
  check(`${mode}: nurse → /patient redirected to /nurse`, path(p).startsWith('/nurse'), path(p))

  // 8. expired access token (Authentication Hardening): renewed from the session (API: the HttpOnly
  // refresh cookie; mock: the mock session) — the user stays signed in. Only when the session itself
  // is over does the page go to sign-in with 「登入已逾時」.
  const expire = () => p.evaluate(() => {
    const s = JSON.parse(sessionStorage.getItem('ccp.auth'))
    s.expiresAt = Date.now() - 1000
    sessionStorage.setItem('ccp.auth', JSON.stringify(s))
    return s.token
  })
  const oldToken = await expire()
  await p.reload({ waitUntil: 'networkidle0' })
  const renewed = await p.evaluate(() => JSON.parse(sessionStorage.getItem('ccp.auth'))?.token)
  check(`${mode}: expired access token → refreshed, still signed in on the same page`, path(p).startsWith('/nurse') && renewed && renewed !== oldToken, path(p))
  // end the session elsewhere, then let the access token expire
  if (mode === 'API') await fetch(`${API_URL}/auth/logout`, { method: 'POST', headers: { Authorization: `Bearer ${renewed}` } })
  else await p.evaluate(async (t) => (await import('/src/mock/api.js')).mockLogout(t), renewed)
  await expire()
  await p.reload({ waitUntil: 'networkidle0' })
  check(`${mode}: expired access token and ended session → /login with 「登入已逾時」`, path(p).startsWith('/login') && (await text(p)).includes('登入已逾時'), path(p))

  if (mode === 'API') {
    // 9. server rejects token (e.g. revoked / tampered) → interceptor logs out
    p = await fresh(base)
    await go(p, '/login')
    await login(p, 'patient01@demo.local')
    await p.evaluate(() => {
      const s = JSON.parse(sessionStorage.getItem('ccp.auth'))
      s.token = s.token.slice(0, -4) + 'AAAA'
      sessionStorage.setItem('ccp.auth', JSON.stringify(s))
    })
    await p.reload({ waitUntil: 'networkidle0' })
    await new Promise((r) => setTimeout(r, 800))
    check('API: tampered token → 401 → interceptor returns to /login', path(p).startsWith('/login'), path(p))
  }
}

await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
