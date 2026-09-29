// Sprint 0: institution settings and the patient home layout come from the API
// (GET /settings/public, GET /dashboard/layout); mock mode returns the same shapes; the built-in
// copy (src/config/defaults.js) is used only when the API request fails.
import { pathToFileURL } from 'node:url'

import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, FRONTEND_DIR, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
// key-order independent JSON (Flask sorts keys, JS keeps insertion order)
const canon = (v) => JSON.stringify(v, (_k, x) => (x && typeof x === 'object' && !Array.isArray(x)
  ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, x[k]])) : x))
const same = (a, b) => canon(a) === canon(b)
const api = async (path, token) => {
  const r = await fetch(API_URL + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  return { status: r.status, body: await r.json() }
}
const login = async (email) => (await (await fetch(`${API_URL}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, password: 'Demo@1234' }) })).json()).data.access_token

// ---------------- contract: API = built-in copy = mock ----------------
const { createServer } = await import(pathToFileURL(`${FRONTEND_DIR}/node_modules/vite/dist/node/index.js`).href)
const vite = await createServer({ root: FRONTEND_DIR, logLevel: 'error', server: { middlewareMode: true, hmr: false }, appType: 'custom' })
const defaults = await vite.ssrLoadModule('/src/config/defaults.js')
const mockConfig = await vite.ssrLoadModule('/src/mock/config.js')

const settings = await api('/settings/public')
check('API settings/public (no token) → 200, meta.source = config_file', settings.status === 200 && same(settings.body.meta, { source: 'config_file' }))
check('contract: API settings = built-in copy (src/config/defaults.js)', same(settings.body.data, defaults.publicSettings))
check('contract: mock settings response = API response (data + meta)', same(mockConfig.mockPublicSettings(), settings.body))

const pt = await login('patient01@demo.local')
const nt = await login('nurse01@demo.local')
const layout = await api('/dashboard/layout', pt)
check('API dashboard/layout (patient) → 200', layout.status === 200, JSON.stringify(layout.body.meta))
check('contract: API layout = built-in copy', same(layout.body.data, defaults.patientLayout))
check('contract: mock layout response = API response (data + meta)', same(mockConfig.mockDashboardLayout('patient'), layout.body))
const nurseLayout = await api('/dashboard/layout', nt)
let mockNurse = null
try { mockConfig.mockDashboardLayout('nurse') } catch (e) { mockNurse = e.status }
check('nurse layout: API 404 and mock 404 (fixed screens in Phase 1)', nurseLayout.status === 404 && mockNurse === 404)
await vite.close()

const itemIds = defaults.patientLayout.items.map((i) => `w-${i.widget_code}`)
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
async function open(base, { block = false } = {}) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  await page.setViewport({ width: 390, height: 900 })
  page.on('pageerror', (e) => errors.push(e.message))
  const seen = []
  page.on('request', (r) => seen.push(r.url()))
  if (block) {
    await page.setRequestInterception(true)
    page.on('request', (r) => (/\/(settings\/public|dashboard\/layout)/.test(r.url()) ? r.abort('failed') : r.continue()))
  }
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  return { page, seen }
}
async function signInPatient(page, email = 'patient01@demo.local') {
  await page.type('input[name=email]', email)
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await idle(page)
  await page.waitForSelector('#w-lab-summary', { timeout: 10000 }).catch(() => {})
}
const orgText = (page) => page.evaluate(() => document.body.innerText)
const widgetOrder = (page) => page.$$eval('[id^="w-"]', (els, ids) => els.map((e) => e.id).filter((id) => ids.includes(id)), itemIds)

// ---------------- API mode ----------------
{
  const { page, seen } = await open(WEB_URL)
  check('API mode: login page shows the institution from /settings/public', (await orgText(page)).includes('Demo 醫院')
    && seen.some((u) => u.endsWith('/settings/public')))
  await signInPatient(page)
  check('API mode: patient home requested /dashboard/layout', seen.some((u) => u.includes('/dashboard/layout')))
  const order = await widgetOrder(page)
  check('API mode: widgets rendered in the API layout order', order.join() === layout.body.data.items.map((i) => `w-${i.widget_code}`).join(), order.join(','))
  await page.screenshot({ path: `${OUT}/config-API-patient.png` })
}

// ---------------- API failure → built-in fallback ----------------
{
  const { page } = await open(WEB_URL, { block: true })
  check('fallback: settings request fails → login page still shows the built-in institution', (await orgText(page)).includes('Demo 醫院'))
  await signInPatient(page)
  const order = await widgetOrder(page)
  check('fallback: layout request fails → home renders with the built-in layout order', order.join() === itemIds.join(), order.join(','))
}

// ---------------- mock mode ----------------
{
  const { page, seen } = await open(MOCK_URL)
  check('mock mode: login page shows the institution', (await orgText(page)).includes('Demo 醫院'))
  await signInPatient(page, 'patient02@demo.local')
  const order = await widgetOrder(page)
  check('mock mode: widgets rendered in the same layout order as API mode', order.join() === itemIds.join(), order.join(','))
  check('mock mode: no request goes to an API', !seen.some((u) => u.includes('/api/v1/')), seen.filter((u) => u.includes('/api/v1/')).slice(0, 2).join(', '))
}

check('no page errors', errors.length === 0, errors.join(' | ').slice(0, 200))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
