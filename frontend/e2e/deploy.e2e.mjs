// Deployment checks against the local Render simulation: static site (:5174) that rewrites /api/*
// to the staging backend (same origin for the browser, like render.yaml), production API build,
// PostgreSQL. API_URL is the backend's own address, used only for server-side checks here.
import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const WEB = WEB_URL
const API = API_URL
const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const idle = (p) => p.waitForNetworkIdle({ idleTime: 500, timeout: 10000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const phone = { width: 390, height: 844, deviceScaleFactor: 1 }

// ---------------- HTTP level: health + static rewrites ----------------
const health = await fetch(`${API}/health`)
const hb = await health.json()
check('health: GET /api/v1/health → 200 {status: ok, database: ok}', health.status === 200 && hb.status === 'ok' && hb.database === 'ok', `${health.status} ${JSON.stringify(hb)}`)
const proxied = await fetch(`${WEB}/api/v1/health`)
check('same-origin: GET <frontend>/api/v1/health is proxied to the backend', proxied.status === 200 && (await proxied.json()).database === 'ok')
const index = await (await fetch(`${WEB}/`)).text()
const nt = (await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: 'nurse01@demo.local', password: 'Demo@1234' }) })).json()).data.access_token
const P1 = (await (await fetch(`${API}/dashboard/widgets/caseload/data`, { headers: { Authorization: `Bearer ${nt}` } })).json()).data[0].patient_id
const DEEP = ['/login', '/patient', '/patient/timeline', '/patient/vitals', '/nurse', '/nurse/notifications', `/nurse/${P1}`]
for (const path of DEEP) {
  const r = await fetch(WEB + path)
  const body = await r.text()
  check(`static rewrite: GET ${path} → 200 index.html (not 404)`, r.status === 200 && body === index && r.headers.get('x-rewritten') === 'true')
}
const asset = index.match(/src="(\/assets\/[^"]+\.js)"/)[1]
const ar = await fetch(WEB + asset)
check('static: /assets/*.js served as a file (not rewritten), immutable cache', ar.status === 200 && ar.headers.get('x-rewritten') === 'false'
  && /javascript/.test(ar.headers.get('content-type')) && /immutable/.test(ar.headers.get('cache-control')))
const js = await ar.text()
check('bundle: same-origin /api/v1, no absolute API URL, no mock data or demo password',
  js.includes('/api/v1') && !js.includes(new URL(API_URL).host) && !js.includes('mock-user') && !js.includes('測試病人') && !js.includes('Demo@1234'))

// ---------------- browser: deep links, network audit ----------------
const traffic = []
async function session() {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  await page.setViewport(phone)
  page.on('request', (r) => traffic.push(r.url()))
  return page
}
const p = await session()
await p.goto(WEB + '/patient/timeline', { waitUntil: 'networkidle0' })
check('deep link before login → login page, remembers the target', new URL(p.url()).pathname === '/login'
  && new URL(p.url()).searchParams.get('redirect') === '/patient/timeline')
await p.type('input[name=email]', 'patient01@demo.local')
await p.type('input[name=password]', 'Demo@1234')
await p.click('button[type=submit]')
await idle(p)
check('after login → back to the deep-linked /patient/timeline', new URL(p.url()).pathname === '/patient/timeline'
  && await p.$('section[data-timeline] li[data-event-type]').then(Boolean))

for (const [path, marker] of [['/patient', '#w-risk-summary'], ['/patient/timeline', 'section[data-timeline] li[data-event-type]'], ['/patient/vitals', 'nav[aria-label="健康紀錄"]']]) {
  await p.goto(WEB + path, { waitUntil: 'networkidle0' })
  const ok = await p.waitForSelector(marker, { timeout: 10000 }).then(() => true).catch(() => false)
  const noOverflow = await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
  check(`patient reload on ${path}: page renders (session kept), 390px no overflow`, ok && new URL(p.url()).pathname === path && noOverflow)
}

const n = await session()
await n.goto(WEB + '/nurse/notifications', { waitUntil: 'networkidle0' })
await n.type('input[name=email]', 'nurse01@demo.local')
await n.type('input[name=password]', 'Demo@1234')
await n.click('button[type=submit]')
await idle(n)
check('nurse deep link /nurse/notifications → login → notification center', new URL(n.url()).pathname === '/nurse/notifications'
  && await n.waitForSelector('section[aria-label="通知列表"]', { timeout: 10000 }).then(() => true).catch(() => false))
for (const [path, marker] of [['/nurse', 'section[aria-labelledby="caseload-title"] li button'], [`/nurse/${P1}`, 'section[aria-labelledby="lab-title"]']]) {
  await n.goto(WEB + path, { waitUntil: 'networkidle0' })
  const ok = await n.waitForSelector(marker, { timeout: 10000 }).then(() => true).catch(() => false)
  check(`nurse reload on ${path}: page renders`, ok && new URL(n.url()).pathname === path)
}
const patientOnNurse = await session()
await patientOnNurse.goto(WEB + '/login', { waitUntil: 'networkidle0' })
await patientOnNurse.type('input[name=email]', 'patient01@demo.local')
await patientOnNurse.type('input[name=password]', 'Demo@1234')
await patientOnNurse.click('button[type=submit]')
await idle(patientOnNurse)
await patientOnNurse.goto(WEB + '/nurse', { waitUntil: 'networkidle0' })
check('patient opening /nurse directly → sent to /patient', new URL(patientOnNurse.url()).pathname === '/patient')

const apiCalls = traffic.filter((u) => u.includes('/api/'))
check('network: every API call stays on the frontend origin (same-origin /api/v1)', apiCalls.length > 10 && apiCalls.every((u) => u.startsWith(`${WEB}/api/v1/`)),
  `${apiCalls.length} calls; others: ${apiCalls.filter((u) => !u.startsWith(WEB)).slice(0, 2).join(', ')}`)
check('network: no mock chunk, the browser never contacts the backend host directly',
  !traffic.some((u) => /\/assets\/api-[\w-]+\.js/.test(u)) && !traffic.some((u) => u.startsWith(new URL(API).origin)))

await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
